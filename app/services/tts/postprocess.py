"""Hậu kỳ từng câu TTS — chạy cho mọi engine trước khi ghép track.

- ``parse_wav``: engine trả nguyên file WAV (Gemini 3.x: header + âm thanh + khối chữ ký C2PA) → chỉ lấy khối «data».
- ``tail_burst``: VieNeu đôi khi sinh thêm một cụm âm sau khi đã đọc xong rồi bị cắt ngang khi còn đang kêu («rẹt»).
- ``polish``: cắt cụm đó, cắt lặng đầu/cuối (RMS 10 ms), fade vào/ra (hết «tách» chỗ nối), cân độ to giữa các câu.
"""

from __future__ import annotations

import os
import struct
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FRAME = 0.010                       # khung 10 ms
GATE_DB = -40.0                     # «có tiếng» = trên đỉnh câu −40 dB …
FLOOR_DB = -55.0                    # … và trên −55 dBFS
BURST_GAP = 0.05                    # cụm âm lạ: cách phần đọc chính ≥ 50 ms lặng
BURST_MAX = 0.45                    # … dài ≤ 450 ms
BURST_MIN_SPEECH = 0.20             # … và phần đọc chính phải ≥ 200 ms (câu cực ngắn thì không cắt)


# ── Đọc / ghi ────────────────────────────────────────────────────────────

def parse_wav(data: bytes) -> tuple[bytes, int, int, int, int] | None:
    """Payload là file WAV → (PCM khối «data», tần số, số kênh, số bit, mã định dạng); không phải WAV → None.
    Chỉ lấy đúng khối «data» theo kích thước khai báo, bỏ mọi khối khác (LIST, C2PA…)."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, rate, ch, bits, tag, pcm = 12, 0, 1, 16, 1, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and size >= 16:
            tag, ch, rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", data, body)
            if tag == 0xFFFE and size >= 26:                 # WAVE_FORMAT_EXTENSIBLE → mã thật nằm ở SubFormat
                tag = struct.unpack_from("<H", data, body + 24)[0]
        elif cid == b"data":
            stream = size in (0, 0xFFFFFFFF) or body + size > len(data)    # WAV ghi dạng luồng: data tới hết file
            pcm = data[body:] if stream else data[body:body + size]
            if stream:
                break
        pos = body + size + (size & 1)
    if pcm is None or not rate:
        return None
    return pcm, rate, max(1, ch), bits, tag


def to_mono16(pcm: bytes, channels: int = 1, bits: int = 16, tag: int = 1) -> bytes:
    """PCM bất kỳ (8/16/24/32 bit, float) nhiều kênh → s16 mono."""
    if channels == 1 and bits == 16 and tag == 1:
        return pcm[: len(pcm) // 2 * 2]
    width = max(1, bits // 8)
    n = len(pcm) // (width * channels) * width * channels
    raw = pcm[:n]
    if tag == 3 and bits == 32:
        a = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    elif tag == 3 and bits == 64:
        a = np.frombuffer(raw, dtype="<f8").astype(np.float32)
    elif bits == 8:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        a = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8 >> 8).astype(np.float32) / 8388608
    elif bits == 32:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648
    else:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1)
    return _to_bytes(a)


def write_wav(path: Path, pcm16: bytes, rate: int) -> Path:
    """Ghi WAV s16 mono qua file tạm riêng từng luồng rồi thay (không để file dở dang, hai luồng không đè nhau)."""
    tmp = path.with_name(f"{path.stem}.{os.getpid()}-{threading.get_ident()}.w{path.suffix}")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm16)
    tmp.replace(path)
    return path


def read_wav_mono16(path: Path) -> tuple[bytes, int] | None:
    """WAV trên đĩa → (s16 mono, tần số). Không đọc được (mp3, WAV lạ…) → None."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    w = parse_wav(data)
    if w is None:
        return None
    pcm, rate, ch, bits, tag = w
    return to_mono16(pcm, ch, bits, tag), rate


# ── Phân tích ────────────────────────────────────────────────────────────

def _to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype="<i2").astype(np.float32) / 32768


def _to_bytes(a: np.ndarray) -> bytes:
    return (np.clip(a, -1.0, 32767 / 32768) * 32768).astype("<i2").tobytes()


def envelope(a: np.ndarray, rate: int) -> np.ndarray:
    """RMS từng khung 10 ms."""
    w = max(1, int(rate * FRAME))
    n = len(a) // w
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt(np.mean(a[: n * w].reshape(n, w) ** 2, axis=1))


@dataclass
class Shape:
    """Hình dạng một câu: khung đầu/cuối có tiếng, ngưỡng, cụm âm lạ cuối câu (nếu có)."""
    first: int                      # khung (10 ms) đầu tiên có tiếng
    last: int                       # khung cuối có tiếng (tính cả cụm âm lạ)
    n: int                          # tổng số khung
    thr: float
    peak: float
    burst_from: int | None = None   # khung bắt đầu khoảng lặng trước cụm âm lạ
    burst_len: float = 0.0          # giây
    max_gap: float = 0.0            # lặng dài nhất giữa phần có tiếng (giây)


def shape(a: np.ndarray, rate: int) -> Shape | None:
    env = envelope(a, rate)
    if len(env) == 0:
        return None
    peak = float(env.max())
    if peak <= 0:
        return None
    thr = max(peak * 10 ** (GATE_DB / 20), 10 ** (FLOOR_DB / 20))
    on = np.where(env > thr)[0]
    if len(on) == 0:
        return None
    sh = Shape(int(on[0]), int(on[-1]), len(env), thr, peak)
    if len(on) > 1:
        sh.max_gap = float((np.diff(on).max() - 1) * FRAME)
    # cụm âm lạ: cụm có tiếng cuối cùng chạm tận cuối file (≤ 30 ms), ngắn, cách phần trước một khoảng lặng
    last = sh.last
    if last >= len(env) - 3:
        start = last
        while start - 1 >= 0 and env[start - 1] > thr:
            start -= 1
        prev = on[on < start]
        if len(prev):
            gap = (start - int(prev[-1]) - 1) * FRAME
            burst = (last - start + 1) * FRAME
            main = (int(prev[-1]) - sh.first + 1) * FRAME
            if gap >= BURST_GAP and burst <= BURST_MAX and main >= BURST_MIN_SPEECH:
                sh.burst_from, sh.burst_len = int(prev[-1]) + 1, burst
    return sh


# ── Xử lý ────────────────────────────────────────────────────────────────

def _fade(a: np.ndarray, rate: int, fade_in: float, fade_out: float) -> np.ndarray:
    """Fade dạng cos nâng (raised-cosine) ở hai đầu — đầu/cuối câu về 0 êm, không «tách»."""
    a = a.copy()
    n_in = min(int(rate * fade_in), len(a) // 2)
    n_out = min(int(rate * fade_out), len(a) // 2)
    if n_in > 1:
        a[:n_in] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n_in, dtype=np.float32))
    if n_out > 1:
        a[-n_out:] *= 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, n_out, dtype=np.float32))
    return a


def active_level_db(a: np.ndarray, rate: int) -> float | None:
    """Độ to phần có tiếng (RMS các khung trên đỉnh −30 dB), dBFS."""
    env = envelope(a, rate)
    if len(env) == 0 or env.max() <= 0:
        return None
    on = env[env > env.max() * 10 ** (-30 / 20)]
    return float(20 * np.log10(np.sqrt(np.mean(on ** 2)) + 1e-12))


def polish(pcm16: bytes, rate: int, *, lead: float = 0.03, tail: float = 0.06, fade_in: float = 0.010,
           fade_out: float = 0.025, level_db: float | None = None, cut_burst: bool = True,
           trim: bool = True) -> bytes:
    """Một câu s16 mono → cắt cụm âm lạ cuối câu, cắt lặng đầu/cuối (chừa ``lead``/``tail`` giây), fade vào/ra,
    đưa độ to phần có tiếng về ``level_db`` dBFS (None = giữ nguyên; tăng tối đa +10 dB, đỉnh ≤ −1 dBFS)."""
    a = _to_float(pcm16)
    if len(a) == 0:
        return pcm16
    sh = shape(a, rate)
    w = max(1, int(rate * FRAME))
    if sh is not None:
        end = len(a)
        if cut_burst and sh.burst_from is not None:
            end = sh.burst_from * w
            last = sh.burst_from - 1
        else:
            last = sh.last
        if trim and sh.n >= 20:
            s = max(0, sh.first * w - int(lead * rate))
            end = min(end, (last + 1) * w + int(tail * rate))
            a = a[s:end]
        else:
            a = a[:end]
    if level_db is not None:
        cur = active_level_db(a, rate)
        if cur is not None:
            gain = 10 ** (max(-20.0, min(10.0, level_db - cur)) / 20)
            peak = float(np.abs(a).max()) * gain
            if peak > 10 ** (-1 / 20):
                gain *= 10 ** (-1 / 20) / peak
            a = a * gain
    return _to_bytes(_fade(a, rate, fade_in, fade_out))


def clean_file(path: Path, *, fade_in: float = 0.010, fade_out: float = 0.025) -> bool:
    """Làm sạch file câu trong cache (giữ tần số gốc): cắt cụm âm lạ cuối câu + fade, không cắt lặng/đổi độ to.
    Không phải WAV → bỏ qua (False)."""
    r = read_wav_mono16(path)
    if r is None:
        return False
    pcm, rate = r
    write_wav(Path(path), polish(pcm, rate, fade_in=fade_in, fade_out=fade_out, trim=False), rate)
    return True
