"""Tự dò vùng chữ cứng (phụ đề in sẵn trên video gốc) để che mờ — mỗi video chữ nằm một chỗ khác nhau.

Cách làm (không cần OCR / model):
1. Lấy ~28 khung rải đều thời lượng, thu về 720 px, ảnh xám.
2. Điểm «giống chữ»: điểm rất sáng nằm sát điểm rất tối (chữ trắng/vàng viền đen — kiểu phụ đề phổ biến), hoặc
   điểm có cạnh rất gắt theo cả hai chiều.
3. Bỏ chữ đứng yên (logo, watermark, tiêu đề cố định): điểm sáng ở gần như mọi khung, hoặc dải chữ mà các khung
   giống hệt nhau — phụ đề thì đổi chữ liên tục.
4. Theo hàng: dải ngang có mật độ «chữ» cao nhất, lặp lại ở nhiều khung → vùng phụ đề; bề ngang lấy theo
   phần chữ rộng nhất (phân vị 95%), nới thêm lề.

Kết quả (toạ độ 0..1 theo khung nguồn) lưu cạnh phụ đề: ``<video>.hardsub.json`` — tính một lần cho mỗi video.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config.paths import SUBS_DIR
from app.core.platform import NO_WINDOW
from app.core.state import VideoItem
from app.services.ffmpeg import FFmpeg

logger = logging.getLogger(__name__)

W = 720
SAMPLES = 28
STYLE = {"strength": 88.0, "feather": 18.0, "opacity": 100.0, "style": "Mờ (Gaussian)", "start": 0.0, "end": 0.0,
         "rounded": 0.0}


def cache_path(item: VideoItem) -> Path:
    h = hashlib.sha1(item.path.encode("utf-8")).hexdigest()[:8]
    stem = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in Path(item.path).stem)[:60]
    return SUBS_DIR / f"{stem}_{h}.hardsub.json"


def cached_region(item: VideoItem) -> dict | None | bool:
    """Vùng đã dò: dict · None (đã dò, không thấy chữ) · False (chưa dò)."""
    p = cache_path(item)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        try:
            if data.get("mtime") != int(Path(item.path).stat().st_mtime):
                return False
        except OSError:
            pass
        return data.get("region")
    except Exception:
        return False


def _frames(path: str, ffmpeg: FFmpeg, duration: float):
    import numpy as np

    times = [duration * (0.04 + 0.92 * k / max(1, SAMPLES - 1)) for k in range(SAMPLES)] if duration > 1 else [0.0]

    def grab(t: float):
        cmd = [ffmpeg.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", path,
               "-frames:v", "1", "-vf", f"scale={W}:-2,format=gray", "-f", "rawvideo", "-"]
        res = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, timeout=60)
        return res.stdout

    with ThreadPoolExecutor(max_workers=4) as pool:
        raws = list(pool.map(grab, times))
    raws = [r for r in raws if r and len(r) % W == 0]
    if not raws:
        return None
    h = len(raws[0]) // W
    frames = [np.frombuffer(r, dtype=np.uint8).reshape(-1, W)[:h] for r in raws if len(r) == len(raws[0])]
    return np.stack(frames) if frames else None


def _dilate(m, r: int):
    import numpy as np

    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                out |= np.roll(np.roll(m, dy, axis=0), dx, axis=1)
    return out


def _text_mask(g):
    """Điểm «giống chữ» của một khung xám."""
    import numpy as np

    g16 = g.astype(np.int16)
    bright = g16 >= 190
    dark = g16 <= 60
    outlined = bright & _dilate(dark, 2)
    gx = np.abs(np.diff(g16, axis=1, prepend=g16[:, :1]))
    gy = np.abs(np.diff(g16, axis=0, prepend=g16[:1, :]))
    sharp = (gx > 90) & (gy > 90)
    m = outlined | _dilate(sharp, 1) & bright
    m[:2, :] = m[-2:, :] = False
    m[:, :2] = m[:, -2:] = False
    return m


def _best_band(masks, score, H):
    """Dải ngang có nhiều «chữ» nhất: (y0, y1, mặt nạ dải, khung có chữ) hoặc None."""
    if score.max() <= 0:
        return None
    peak = int(score.argmax())
    thr = score[peak] * 0.3
    y0 = y1 = peak
    limit = int(H * 0.30)
    while y0 > 0 and score[y0 - 1] >= thr and y1 - y0 < limit:
        y0 -= 1
    while y1 < H - 1 and score[y1 + 1] >= thr and y1 - y0 < limit:
        y1 += 1
    band = masks[:, y0:y1 + 1, :]
    hit = band.reshape(len(masks), -1).mean(axis=1) > 0.003
    if hit.mean() < 0.2 or (y1 - y0) < 5:              # chữ xuất hiện quá ít khung → không phải phụ đề
        return None
    return y0, y1, band, hit


def _is_static(band, hit) -> bool:
    """Chữ trong dải gần như giống nhau ở mọi khung có chữ → watermark / tiêu đề cố định, không phải phụ đề."""
    import numpy as np

    idx = np.where(hit)[0]
    if len(idx) < 3:
        return False
    m = [_dilate(band[i], 1) for i in idx]
    ious = []
    for a in range(len(m)):
        for b in range(a + 1, len(m)):
            inter = np.logical_and(m[a], m[b]).sum()
            union = np.logical_or(m[a], m[b]).sum()
            if union:
                ious.append(inter / union)
    return bool(ious) and float(np.median(ious)) > 0.45


def detect_region(path: str, ffmpeg: FFmpeg, duration: float) -> dict | None:
    """Vùng phụ đề cứng {x, y, w, h (0..1), …kiểu che mặc định} hoặc None nếu không thấy."""
    import numpy as np

    frames = _frames(path, ffmpeg, duration)
    if frames is None or len(frames) < 4:
        return None
    n, H, Wd = frames.shape
    masks = np.stack([_text_mask(f) for f in frames])
    static = masks.mean(axis=0) > 0.8                 # logo / watermark / khung cố định
    masks &= ~static
    rows = masks.mean(axis=2)                          # n × H: mật độ chữ theo hàng mỗi khung
    present = rows > 0.008                             # hàng có chữ ở khung đó
    score = rows.mean(axis=0) * (0.5 + present.mean(axis=0))
    # dải dưới khung (phụ đề hay nằm đó) được ưu tiên nhẹ
    score = score * (1.0 + 0.3 * (np.arange(H) > H * 0.5))
    for _attempt in range(3):
        found = _best_band(masks, score, H)
        if found is None:
            return None
        y0, y1, band, hit = found
        if not _is_static(band, hit):
            break
        score[max(0, y0 - 2):y1 + 3] = 0               # chữ đứng yên (watermark / tiêu đề) → tìm dải khác
    else:
        return None
    xs0, xs1 = [], []
    for f in np.where(hit)[0]:
        cols = np.where(band[f].any(axis=0))[0]
        if len(cols) >= 3:
            xs0.append(cols[0])
            xs1.append(cols[-1])
    if not xs0:
        return None
    x0, x1 = float(np.percentile(xs0, 5)), float(np.percentile(xs1, 95))
    # phụ đề gần như luôn căn giữa → vùng che đối xứng qua tâm (khỏi sót nửa câu dài khi khung mẫu chỉ có câu ngắn)
    x0, x1 = min(x0, Wd - 1 - x1), max(x1, Wd - 1 - x0)
    pad_x, pad_y = Wd * 0.04, max(4.0, (y1 - y0) * 0.6)
    x0, x1 = max(0.0, x0 - pad_x), min(Wd - 1.0, x1 + pad_x)
    yy0, yy1 = max(0.0, y0 - pad_y), min(H - 1.0, y1 + pad_y)
    region = {"x": round(x0 / Wd, 4), "y": round(yy0 / H, 4), "w": round((x1 - x0) / Wd, 4),
              "h": round((yy1 - yy0) / H, 4), **STYLE, "auto": True,
              "confidence": round(float(hit.mean()), 2)}
    return region


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def ensure_region(item: VideoItem, ffmpeg: FFmpeg, duration: float, log=None) -> dict | None:
    """Vùng chữ cứng của ``item`` (dò một lần, lưu cache). Khung xem trước và bước Chuẩn bị cùng hỏi một video thì
    lượt sau chờ lượt đang dò rồi dùng kết quả — không dò hai lần cùng lúc."""
    with _locks_guard:
        lock = _locks.setdefault(str(cache_path(item)), threading.Lock())
    with lock:
        return _ensure_region(item, ffmpeg, duration, log)


def _ensure_region(item: VideoItem, ffmpeg: FFmpeg, duration: float, log=None) -> dict | None:
    got = cached_region(item)
    if got is not False:
        return got
    try:
        region = detect_region(item.path, ffmpeg, duration)
    except Exception:
        logger.warning("Dò chữ cứng lỗi: %s", item.path, exc_info=True)
        if log:
            log(f"{item.name}: dò chữ cứng lỗi — lần sau sẽ dò lại (hoặc bấm «Dò lại» ở tab Che mờ).")
        return None
    try:
        SUBS_DIR.mkdir(parents=True, exist_ok=True)
        cache_path(item).write_text(json.dumps({"region": region, "mtime": int(Path(item.path).stat().st_mtime)},
                                               ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    if log:
        if region:
            log(f"{item.name}: thấy chữ cứng ở {region['y'] * 100:.0f}–{(region['y'] + region['h']) * 100:.0f}% chiều cao "
                f"(có ở {region['confidence'] * 100:.0f}% khung) → che mờ vùng này.")
        else:
            log(f"{item.name}: không thấy chữ cứng — không che.")
    return region


def forget(item: VideoItem) -> None:
    cache_path(item).unlink(missing_ok=True)
