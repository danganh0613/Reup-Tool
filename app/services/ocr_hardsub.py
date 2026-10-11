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
from app.core.state import Settings, VideoItem
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
_HAN = re.compile(r"[㐀-䶿一-鿿]")    # gồm cả chữ Hán hiếm (䴙䴘 — tên chim)
_ocr = None
_cc = None
_lock = threading.Lock()


def _load(log: Callable[[str], None] | None = None):
    global _ocr
    with _lock:
        if _ocr is None:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(MODELS_DIR / "paddlex"))
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            try:
                from paddleocr import PaddleOCR
            except Exception as exc:                   # noqa: BLE001 — bản đóng gói thiếu PaddleOCR / thư viện hỏng
                logger.warning("Không nạp được PaddleOCR", exc_info=True)
                raise NoHardsub("Máy này chưa đọc được chữ in sẵn (thiếu bộ đọc chữ PaddleOCR)") from exc

            if log:
                log("Nạp PaddleOCR (PP-OCRv5 mobile)… lần đầu tải model ~22 MB")
            try:
                # enable_mkldnn=False: Paddle 3.x trên Windows CPU bật oneDNN thì lỗi «ConvertPirAttribute2RuntimeAttribute»
                _ocr = PaddleOCR(lang="ch", text_detection_model_name="PP-OCRv5_mobile_det",
                                 text_recognition_model_name="PP-OCRv5_mobile_rec", use_doc_orientation_classify=False,
                                 use_doc_unwarping=False, use_textline_orientation=False, device="cpu",
                                 enable_mkldnn=False)
            except Exception as exc:                   # noqa: BLE001 — thiếu cấu hình / DLL, chưa tải được model…
                logger.warning("Không khởi tạo được PaddleOCR", exc_info=True)
                raise NoHardsub(f"Không mở được bộ đọc chữ PaddleOCR ({str(exc).strip()[:120] or type(exc).__name__})") from exc
        return _ocr


def available() -> bool:
    """Có thư viện đọc chữ (PaddleOCR) không — chỉ dò gói, không nạp (nạp mất vài giây)."""
    from importlib.util import find_spec

    try:
        if find_spec("paddleocr") is None or find_spec("paddle") is None:
            return False
        pdx = find_spec("paddlex")
    except (ImportError, ValueError):
        return False
    # bản đóng gói thiếu cấu hình pipeline của PaddleX → nạp sẽ lỗi «The pipeline (OCR) does not exist»
    roots = list(pdx.submodule_search_locations or []) if pdx is not None else []
    return any(os.path.isfile(os.path.join(r, "configs", "pipelines", "OCR.yaml")) for r in roots)


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
    if any(_HAN.search(t) for t in lines):
        # có phụ đề chữ Hán → bỏ các khung chữ lẻ không có chữ Hán cùng dải (số «008», logo «Oog»… lọt vào câu dịch)
        lines = [t for t in lines if _HAN.search(t)]
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


class NoHardsub(RuntimeError):
    """Video không thấy phụ đề in sẵn — bước nhận diện tự chuyển sang nghe giọng nói nếu đã cài Qwen3-ASR."""


def _speech_tokens(item: VideoItem, ffmpeg: FFmpeg, *, log: Callable[[str], None] | None,
                   cancel: threading.Event | None) -> list[tuple[str, float, float]]:
    """Mốc từng chữ của lời nói bằng SenseVoice (cần torch — bản .exe không có thì [])."""
    try:
        from app.services.asr_funasr import recognize_tokens

        tokens, _lang, _total = recognize_tokens(item.path, ffmpeg, language="zh", log=log, cancel=cancel)
        return tokens
    except RenderCancelled:
        raise
    except Exception as exc:                            # noqa: BLE001 — thiếu FunASR / torch → giữ mốc chữ trên hình
        logger.warning("Không lấy được mốc tiếng nói cho OCR: %s", exc)
        return []


def read(item: VideoItem, ffmpeg: FFmpeg, *, duration: float, on_progress: Callable[[float], None] | None = None,
         log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> list[Cue]:
    """Đọc phụ đề in sẵn → từng dòng chữ với mốc lúc chữ hiện trên hình. Không có phụ đề in sẵn → ``NoHardsub``."""
    from app.services import hardsub

    info = ffmpeg.probe(item.path)
    reg = hardsub.ensure_region(item, ffmpeg, duration, log=log, cancel=cancel)
    if not reg:
        raise NoHardsub("Không thấy phụ đề in sẵn trên video.")
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
                on_progress(min(0.95, 0.95 * i / FPS / duration))
    finally:
        proc.stdout.close()
        proc.wait()
    cues = segments_to_cues(merge(frames, 1.0 / FPS))
    if log:
        log(f"Đọc chữ in sẵn: {len(cues)} dòng · {calls} lượt OCR / {i} khung")
    return cues


def finish(item: VideoItem, ffmpeg: FFmpeg, cues: list[Cue], *, speech_timing: bool = True,
           settings: Settings | None = None, log: Callable[[str], None] | None = None,
           cancel: threading.Event | None = None) -> STTResult:
    """Chỉ có chữ in sẵn (máy chưa cài Qwen3-ASR / video không có tiếng): dời mốc theo tiếng nói (SenseVoice), AI gộp
    các dòng chữ cùng một câu (``settings`` — ``translate_vl.group_fragments``)."""
    han = any(_HAN.search(c.text) for c in cues)
    if speech_timing and cues and han:
        tokens = _speech_tokens(item, ffmpeg, log=log, cancel=cancel)
        if tokens:
            cues, moved = retime(cues, tokens)
            if log:
                log(f"Mốc theo tiếng nói (SenseVoice): dời {moved}/{len(cues)} câu")
    if settings is not None and len(cues) > 1:
        from app.services.translate_vl import group_fragments

        cues = group_fragments(cues, settings=settings, language="zh" if han else "", log=log, cancel=cancel)
    return STTResult(cues=cues, language="zh" if han else "", language_probability=1.0)


def transcribe(item: VideoItem, ffmpeg: FFmpeg, *, duration: float, speech_timing: bool = True,
               settings: Settings | None = None, on_progress: Callable[[float], None] | None = None,
               log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> STTResult:
    cues = read(item, ffmpeg, duration=duration, on_progress=on_progress, log=log, cancel=cancel)
    res = finish(item, ffmpeg, cues, speech_timing=speech_timing, settings=settings, log=log, cancel=cancel)
    if on_progress:
        on_progress(1.0)
    return res


# ── Sửa lời nghe được theo chữ in trên video ─────────────────────────────

_CJK_CHAR = "぀-ヿ㐀-鿿가-힯"
_TOKEN = re.compile(f"[{_CJK_CHAR}]|[^\\W_{_CJK_CHAR}]+")
FIX_MAX = 6               # một chỗ sửa tối đa ngần này chữ / từ (dài hơn = hai bên nói khác hẳn → giữ lời nghe được)
FIX_NEAR = 2.0            # chữ trên hình phải hiện trong khoảng ± ngần này giây quanh câu nghe được


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def token_count(text: str) -> int:
    """Số chữ (Hán / Kana / Hangul) hoặc số từ (chữ Latin…) — để so lời nghe được với chữ trên hình."""
    return len(_TOKEN.findall(text))


def _readings(ch: str) -> set[str]:
    """Các cách đọc (pinyin bỏ thanh) của một chữ Hán — thiếu pypinyin thì rỗng (không kiểm âm)."""
    try:
        from pypinyin import Style, pinyin
    except ImportError:
        return set()
    return set(pinyin(ch, style=Style.NORMAL, heteronym=True)[0])


def _sounds_alike(heard: list[str], shown: list[str]) -> bool:
    """Chỗ khác nhau là do Qwen NGHE sai (nên sửa) chứ không phải OCR đọc nhầm chữ trông giống: nghe sai thì ra chữ ĐỌC
    giống (钱/钳 qián, 鹿/鹭 lù, 沃伦/涡轮); OCR đọc nhầm thì ra chữ TRÔNG giống mà đọc khác (白 bái / 自 zì → giữ chữ
    nghe được). Qwen ra chữ Latin cho tên hiếm (PT → 䴙䴘) → sửa. Chữ Latin… → viết gần giống nhau."""
    def han(t: str) -> bool:
        return bool(_HAN.match(t))

    if all(han(t) for t in shown) and any(not han(t) for t in heard):
        return True
    if all(han(t) for t in heard + shown):
        reads = [(_readings(a), _readings(b)) for a, b in zip(heard, shown)]
        if any(not ra or not rb for ra, rb in reads):
            return True                                 # không kiểm được âm → tin chữ trên hình như trước
        if len(heard) == len(shown):                    # cả cụm có ≥ nửa số chữ đọc giống («钱嘴冠» → «钳嘴鹤»: 2/3)
            return sum(1 for ra, rb in reads if ra & rb) * 2 >= len(heard)

        def say(ts: list[str]) -> str:
            return " ".join(min(_readings(t) or {t}) for t in ts)

        return difflib.SequenceMatcher(None, say(heard), say(shown)).ratio() >= 0.6
    return difflib.SequenceMatcher(None, " ".join(heard).lower(), " ".join(shown).lower()).ratio() >= 0.5


def correct_with_ocr(cues: list[Cue], ocr_cues: list[Cue]) -> tuple[list[Cue], list[tuple[str, str]]]:
    """Câu + mốc của Qwen3-ASR (chia câu đúng nhịp nói) nhưng chữ nghe sai thay bằng chữ in trên video (đúng như creator
    gõ — tên riêng: «钱嘴冠» → «钳嘴鹤», «PT» → «䴙䴘»). Dò toàn bộ lời nghe được với toàn bộ chữ trên hình theo thứ tự;
    chỉ thay đoạn ngắn nằm giữa hai đoạn khớp, dài tương đương, và chữ đó hiện trên hình gần lúc câu được nói. Lời nói
    không có trên phụ đề → giữ; chữ trên hình không ai nói (tiêu đề, logo) → bỏ qua. → (câu mới, [(cũ, mới)])."""
    cjk = sum(1 for c in cues if re.search(f"[{_CJK_CHAR}]", c.plain)) * 2 >= max(1, len(cues))
    texts = [c.plain.replace("\n", "" if cjk else " ") for c in cues]
    a = [(tok.lower(), k, s, e) for k, t in enumerate(texts) for tok, s, e in _tokens(t)]
    b = [(tok, c.start, c.end) for c in ocr_cues for tok, _s, _e in _tokens(c.plain)]
    if not a or not b:
        return cues, []
    sm = difflib.SequenceMatcher(None, [x[0] for x in a], [x[0].lower() for x in b], autojunk=False)
    ops = sm.get_opcodes()
    edits: dict[int, list[tuple[int, int, str]]] = {}
    fixes: list[tuple[str, str]] = []
    n = 0
    while n < len(ops):
        if ops[n][0] != "replace":
            n += 1
            continue
        m = n                                           # chỗ khác cách nhau đúng một chữ giống → xét cả cụm («钱嘴冠»)
        while m + 2 < len(ops) and ops[m + 1][0] == "equal" and ops[m + 1][2] - ops[m + 1][1] == 1 \
                and ops[m + 2][0] == "replace":
            m += 2
        _op, i1, _i2, j1, _j2 = ops[n]
        i2, j2 = ops[m][2], ops[m][4]
        anchored = n > 0 and ops[n - 1][0] == "equal" and m + 1 < len(ops) and ops[m + 1][0] == "equal"
        n = m + 1
        na, nb = i2 - i1, j2 - j1
        k = a[i1][1]
        if not anchored or na > FIX_MAX or nb > FIX_MAX or not 0.5 <= nb / na <= 2 or any(x[1] != k for x in a[i1:i2]):
            continue
        cs, ce = cues[k].start - FIX_NEAR, cues[k].end + FIX_NEAR
        if not all(cs <= s and e <= ce for _t, s, e in b[j1:j2]):
            continue
        if not _sounds_alike([texts[k][x[2]:x[3]] for x in a[i1:i2]], [t for t, _s, _e in b[j1:j2]]):
            continue
        new = ("" if cjk else " ").join(t for t, _s, _e in b[j1:j2])
        edits.setdefault(k, []).append((a[i1][2], a[i2 - 1][3], new))
    out: list[Cue] = []
    for k, c in enumerate(cues):
        t = texts[k]
        if k in edits:
            lo, hi = min(x[0] for x in edits[k]), max(x[1] for x in edits[k])
            for s, e, new in sorted(edits[k], reverse=True):
                t = t[:s] + new + t[e:]
            fixes.append((texts[k][lo:hi], t[lo:hi + len(t) - len(texts[k])]))     # cả cụm: «钱嘴冠» → «钳嘴鹤»
        out.append(Cue(c.index, c.start, c.end, wrap_lines(t) if k in edits else c.text, dict(c.meta)))
    return out, fixes
