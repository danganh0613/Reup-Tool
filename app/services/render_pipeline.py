"""Dựng lệnh FFmpeg cho một video từ cấu hình các module.

Nhánh video: [nguồn] → cắt (trim) → tốc độ → lật → zoom → màu/hiệu ứng → khung hình
→ fps → các lớp phủ (blur vùng, logo, chữ) → phụ đề → yuv420p → [vout].

Mỗi module góp một ``Stage``: hàm nhận (graph, nhãn_vào, ctx) và trả nhãn_ra.
``ctx.t0`` là mốc thời gian gốc khi xem trước (khung hình lấy bằng -ss nên t=0),
mọi biểu thức theo thời gian phải dùng ``ctx.t()`` thay vì ``t``.

Tất cả file phụ (ASS, mask, text) đặt trong thư mục làm việc và tham chiếu bằng
tên tương đối, tránh vấn đề escape đường dẫn Windows trong filter.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.platform import NO_WINDOW
from app.core.state import (ENCODE_PRESETS, FIT_MODES, RATIO_VALUES, RESOLUTIONS, VIDEO_CODECS, VideoConfig,
                            VideoItem)
from app.services.ffmpeg import FFmpeg, MediaInfo, RenderCancelled, atempo_chain, cpu_may_help
from app.services.srt_utils import Cue

logger = logging.getLogger(__name__)

_RATIO_VALUE = {k: v for k, v in RATIO_VALUES.items() if v}
_RES_SHORT = {RESOLUTIONS[1]: 720, RESOLUTIONS[2]: 1080, RESOLUTIONS[3]: 1440, RESOLUTIONS[4]: 2160}

MASTER_TP = -1.5        # trần true-peak của phần tiếng trước khi nén AAC (AAC đẩy đỉnh thêm ~0,3–0,5 dB → file ≤ −1 dBTP)
DUCK_DB = -15.0         # nhạc nền / âm gốc hạ bấy nhiêu dB khi có giọng
DUCK_HOLD = 1.0         # khoảng nghỉ giữa hai câu ngắn hơn → giữ nguyên mức hạ (không «bơm» theo từng câu)
DUCK_ATTACK = 0.15      # bắt đầu hạ trước câu
DUCK_RELEASE = 0.40     # trả lại sau câu
#: chỗ không có giọng lâu hơn ``DUCK_HOLD`` (video gốc tự ngừng nói, đầu / cuối video) âm gốc dâng tới ×3 mức đặt (5 % →
#: 15 %), tối đa 25 %: nhạc / tiếng động lấp chỗ trống như dựng tay, dưới giọng vẫn hạ như cũ. Âm gốc 0 % = tắt hẳn
SWELL = 3.0
SWELL_MAX = 25.0


def _even(n: float) -> int:
    v = int(round(n))
    return max(2, v - (v % 2))


# ── Hình học khung ra ─────────────────────────────────────────────────────

@dataclass
class OutputGeometry:
    width: int
    height: int
    mode: str            # "same" | "crop" | "pad" | "blur" | "scale"


def output_geometry(info: MediaInfo, cfg: VideoConfig) -> OutputGeometry:
    """Kích thước khung ra và cách đưa nguồn vào khung."""
    sw, sh = max(2, info.width), max(2, info.height)
    ratio = _RATIO_VALUE.get(cfg.ratio)
    short = _RES_SHORT.get(cfg.resolution)

    if ratio is None:
        if short is None:
            return OutputGeometry(sw, sh, "same")
        if sw >= sh:
            return OutputGeometry(_even(short * sw / sh), _even(short), "scale")
        return OutputGeometry(_even(short), _even(short * sh / sw), "scale")

    mode = {FIT_MODES[0]: "crop", FIT_MODES[1]: "pad", FIT_MODES[2]: "blur"}.get(cfg.fit_mode, "crop")
    if short is None:
        if mode == "crop":
            # hộp lớn nhất theo tỉ lệ đích nằm trọn trong nguồn (không phóng to)
            if ratio >= 1:
                w, h = sw, sw / ratio
                if h > sh:
                    h, w = sh, sh * ratio
            else:
                h, w = sh, sh * ratio
                if w > sw:
                    w, h = sw, sw / ratio
        else:
            # giữ cạnh dài của nguồn, nguồn co lại nằm trong khung
            long_side = max(sw, sh)
            if ratio >= 1:
                w, h = long_side, long_side / ratio
            else:
                h, w = long_side, long_side * ratio
    else:
        if ratio >= 1:
            h, w = short, short * ratio
        else:
            w, h = short, short / ratio
    return OutputGeometry(_even(w), _even(h), mode)


# ── Graph builder ─────────────────────────────────────────────────────────

class Graph:
    """Gom các đoạn filter_complex và cấp nhãn/chỉ số input mới."""

    def __init__(self, first_extra_input: int = 1) -> None:
        self.parts: list[str] = []
        self.extra_inputs: list[list[str]] = []      # mỗi phần tử là args cho một -i (vd ["-loop","1","-i",path])
        self._next_input = first_extra_input
        self._n = 0

    def label(self, prefix: str = "l") -> str:
        self._n += 1
        return f"[{prefix}{self._n}]"

    def add(self, text: str) -> None:
        if text:
            self.parts.append(text)

    def chain(self, src: str, filters: list[str], out: str | None = None) -> str:
        """``src`` → filters → nhãn ra. Không có filter thì trả lại ``src``."""
        filters = [f for f in filters if f]
        if not filters:
            return src
        out = out or self.label()
        self.parts.append(f"{src}{','.join(filters)}{out}")
        return out

    def add_input(self, args: list[str]) -> int:
        """Đăng ký một input phụ, trả về chỉ số của nó trong lệnh ffmpeg."""
        self.extra_inputs.append(args)
        idx = self._next_input
        self._next_input += 1
        return idx

    def text(self) -> str:
        return ";".join(self.parts)


@dataclass
class RenderContext:
    width: int
    height: int
    fps: float
    duration: float          # thời lượng sau cắt/đổi tốc độ
    work_dir: Path           # nơi ghi mask/text phụ
    t0: float = 0.0          # mốc thời gian gốc khi xem trước
    preview: bool = False
    speed: float = 1.0
    src_width: int = 0       # kích thước khung nguồn (stage chạy trước khi đổi khung dùng cái này)
    src_height: int = 0
    src_t0: float = 0.0      # mốc thời gian theo nguồn (trước khi đổi tốc độ / cắt)
    px: float = 1.0          # hệ số cỡ điểm ảnh so với bản render (phát xem trước ở cỡ nhỏ: độ mờ… co theo)

    def t(self) -> str:
        """Biểu thức thời gian tuyệt đối dùng trong filter."""
        return f"(t+{self.t0:.3f})" if self.t0 else "t"

    def t_src(self) -> str:
        """Biểu thức thời gian theo nguồn — cho stage áp lên khung nguồn (che mờ)."""
        return f"(t+{self.src_t0:.3f})" if self.src_t0 else "t"


Stage = Callable[[Graph, str, RenderContext], str]


@dataclass
class TrimSpec:
    """Đoạn giữ lại (giây, theo nguồn). Rỗng = không cắt."""
    segments: list[tuple[float, float]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.segments)

    @property
    def kept_duration(self) -> float:
        return sum(max(0.0, e - s) for s, e in self.segments)


@dataclass
class VideoGraph:
    graph: Graph
    vout: str
    audio_src: str | None      # nhãn audio nguồn sau cắt, None nếu nguồn không có audio
    input_args: list[str]      # tuỳ chọn đặt trước -i nguồn (vd -ss/-to khi cắt một đoạn)
    ctx: RenderContext


def _offsets(cfg: VideoConfig) -> tuple[float, float]:
    """Lệch canh nội dung (−0,5…0,5) theo ``offset_x`` / ``offset_y`` (−50…50 %)."""
    return (max(-0.5, min(0.5, getattr(cfg, "offset_x", 0) / 100.0)),
            max(-0.5, min(0.5, getattr(cfg, "offset_y", 0) / 100.0)))


def _place(free: float, off: float) -> float:
    """Vị trí đầu (theo cùng đơn vị với ``free``) khi canh trong khoảng trống ``free`` (âm = nguồn lớn hơn khung, phần
    thừa bị cắt): lệch dương luôn đưa NỘI DUNG sang phải / xuống dưới."""
    return free * (0.5 + off) if free >= 0 else free * (0.5 - off)


def source_rect_in_output(info: MediaInfo, cfg: VideoConfig) -> tuple[float, float, float, float]:
    """Vị trí khung nguồn (sau phóng to) trong khung ra, theo tỉ lệ khung ra: (ox, oy, sx, sy).
    Điểm nguồn (x, y) ∈ [0,1]² (sau lật/phóng) nằm tại (ox + x·sx, oy + y·sy) trong khung ra; với chế độ cắt vừa
    khung, ox/oy có thể âm và sx/sy > 1 (phần nguồn bị cắt bỏ)."""
    geo = output_geometry(info, cfg)
    sw, sh = max(2, info.width), max(2, info.height)
    W, H = max(2, geo.width), max(2, geo.height)
    if geo.mode in ("crop",):
        k = max(W / sw, H / sh)
    elif geo.mode in ("pad", "blur"):
        k = min(W / sw, H / sh)
    else:
        return 0.0, 0.0, 1.0, 1.0
    dw, dh = sw * k, sh * k
    ox, oy = _offsets(cfg)
    return _place(W - dw, ox) / W, _place(H - dh, oy) / H, dw / W, dh / H


def source_to_output(info: MediaInfo, cfg: VideoConfig, x: float, y: float) -> tuple[float, float]:
    """Điểm (x, y) theo tỉ lệ khung NGUỒN → tỉ lệ khung RA (qua lật gương, phóng to, đổi khung)."""
    zoom = max(1.0, min(4.0, cfg.zoom / 100.0))
    if cfg.flip_h:
        x = 1.0 - x
    zx, zy = _zoom_origin(cfg, zoom)
    x, y = (x - zx) * zoom, (y - zy) * zoom
    ox, oy, sx, sy = source_rect_in_output(info, cfg)
    return ox + x * sx, oy + y * sy


def _zoom_origin(cfg: VideoConfig, zoom: float) -> tuple[float, float]:
    """Góc trên-trái vùng phóng to (theo tỉ lệ khung nguồn) — vùng phóng đi theo lệch canh (0 = giữa)."""
    ox, oy = _offsets(cfg)
    return _place(1.0 - 1.0 / zoom, -ox) if zoom > 1.001 else 0.0, _place(1.0 - 1.0 / zoom, -oy) if zoom > 1.001 else 0.0


def output_to_source(info: MediaInfo, cfg: VideoConfig, x: float, y: float) -> tuple[float, float]:
    """Ngược của ``source_to_output``: điểm theo tỉ lệ khung RA → tỉ lệ khung NGUỒN (có thể nằm ngoài 0..1)."""
    zoom = max(1.0, min(4.0, cfg.zoom / 100.0))
    ox, oy, sx, sy = source_rect_in_output(info, cfg)
    x, y = (x - ox) / max(1e-9, sx), (y - oy) / max(1e-9, sy)
    zx, zy = _zoom_origin(cfg, zoom)
    x, y = x / zoom + zx, y / zoom + zy
    if cfg.flip_h:
        x = 1.0 - x
    return x, y


def build_video_graph(info: MediaInfo, cfg: VideoConfig, *, work_dir: Path, ass_name: str | None = None,
                      stages: list[Stage] | None = None, pre_stages: list[Stage] | None = None,
                      src_stages: list[Stage] | None = None,
                      trim: TrimSpec | None = None, include_fps: bool = True, t0: float = 0.0,
                      preview: bool = False, first_extra_input: int = 1, preview_scale: float = 1.0,
                      extend: float = 0.0) -> VideoGraph:
    """Dựng toàn bộ nhánh video. ``src_stages`` áp thẳng lên khung nguồn (che mờ — toạ độ theo video gốc, đi theo
    mọi kiểu khung ra); ``pre_stages`` chạy sau lật/phóng nhưng trước khi đổi khung (màu, hiệu ứng).
    ``extend``: giữ khung cuối thêm bấy nhiêu giây (giọng dài hơn video → kéo dài hình, không cắt giọng).

    ``preview_scale`` < 1 (chỉ khi ``preview``): xử lý cả chuỗi ở cỡ nhỏ (phát xem trước theo thời gian thực) —
    nguồn thu nhỏ trước, khung ra cùng tỉ lệ; vị trí/cỡ của các lớp phủ tính theo tỉ lệ khung nên không đổi."""
    g = Graph(first_extra_input)
    geo = output_geometry(info, cfg)
    k = preview_scale if preview and 0.05 < preview_scale < 0.999 else 1.0
    if k < 1.0:
        geo = OutputGeometry(_even(geo.width * k), _even(geo.height * k), geo.mode)
    speed = max(0.25, min(4.0, cfg.speed / 100.0))
    zoom = max(1.0, min(4.0, cfg.zoom / 100.0))
    src_dur = info.duration
    input_args: list[str] = []
    vsrc, asrc = "[0:v]", ("[0:a]" if info.has_audio else None)
    src_w, src_h = max(2, info.width), max(2, info.height)
    if k < 1.0:
        src_w, src_h = _even(src_w * k), _even(src_h * k)
        vsrc = g.chain(vsrc, [f"scale={src_w}:{src_h}"])

    # ── Cắt ──────────────────────────────────────────────────────────────
    src_offset = 0.0
    if trim is not None and trim.active and not preview:
        segs = [(max(0.0, s), min(src_dur or e, e)) for s, e in trim.segments if e > s]
        if len(segs) == 1:
            s, e = segs[0]
            input_args = ["-ss", f"{s:.3f}", "-to", f"{e:.3f}"]
            src_dur = e - s
            src_offset = s          # pts nguồn bắt đầu lại từ 0 sau -ss
        elif segs:
            vlabels, alabels = [], []
            for i, (s, e) in enumerate(segs):
                v = g.chain("[0:v]", [f"trim=start={s:.3f}:end={e:.3f}", "setpts=PTS-STARTPTS"], f"[tv{i}]")
                vlabels.append(v)
                if asrc:
                    a = g.chain("[0:a]", [f"atrim=start={s:.3f}:end={e:.3f}", "asetpts=PTS-STARTPTS"], f"[ta{i}]")
                    alabels.append(a)
            if asrc:
                pairs = "".join(f"{v}{a}" for v, a in zip(vlabels, alabels))
                g.add(f"{pairs}concat=n={len(segs)}:v=1:a=1[tvcat][tacat]")
                vsrc, asrc = "[tvcat]", "[tacat]"
            else:
                g.add(f"{''.join(vlabels)}concat=n={len(segs)}:v=1:a=0[tvcat]")
                vsrc = "[tvcat]"
            src_dur = sum(e - s for s, e in segs)

    extend = max(0.0, extend) if not preview else 0.0
    ctx = RenderContext(width=geo.width, height=geo.height, fps=info.fps or 30.0,
                        duration=(src_dur / speed if speed else src_dur) + extend, work_dir=work_dir, t0=t0,
                        preview=preview, speed=speed, src_width=src_w, src_height=src_h,
                        src_t0=t0 * speed + src_offset, px=k)

    # ── Stage trên khung nguồn (che mờ) ──────────────────────────────────
    cur = vsrc
    for st in src_stages or []:
        cur = st(g, cur, ctx)

    # ── Biến đổi cơ bản ──────────────────────────────────────────────────
    pre: list[str] = []
    if abs(speed - 1.0) > 0.001:
        pre.append(f"setpts=PTS/{speed:.4f}")
    if cfg.flip_h:
        pre.append("hflip")
    if zoom > 1.001:
        zx, zy = _zoom_origin(cfg, zoom)
        pre.append(f"crop=iw/{zoom:.4f}:ih/{zoom:.4f}:iw*{zx:.4f}:ih*{zy:.4f}")
    cur = g.chain(cur, pre)

    for st in pre_stages or []:
        cur = st(g, cur, ctx)

    # ── Khung hình ───────────────────────────────────────────────────────
    W, H = geo.width, geo.height
    if geo.mode == "blur":
        bg_src, fg_src = g.label("bgsrc"), g.label("fgsrc")
        g.add(f"{cur}split=2{bg_src}{fg_src}")
        bg = g.chain(bg_src, [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}",
                              f"gblur=sigma={28 * k:.1f}", "eq=brightness=-0.08"])
        fg = g.chain(fg_src, [f"scale={W}:{H}:force_original_aspect_ratio=decrease"])
        cur = g.label()
        ox, oy = _offsets(cfg)                          # canh nội dung: lệch trong khoảng trống (0 = giữa)
        g.add(f"{bg}{fg}overlay=(W-w)*{0.5 + ox:.4f}:(H-h)*{0.5 + oy:.4f}{cur}")
    elif geo.mode == "pad":
        ox, oy = _offsets(cfg)
        cur = g.chain(cur, [f"scale={W}:{H}:force_original_aspect_ratio=decrease",
                            f"pad={W}:{H}:(ow-iw)*{0.5 + ox:.4f}:(oh-ih)*{0.5 + oy:.4f}:black"])
    elif geo.mode == "crop":
        ox, oy = _offsets(cfg)                          # chọn phần giữ lại: lệch dương = nội dung sang phải / xuống
        cur = g.chain(cur, [f"scale={W}:{H}:force_original_aspect_ratio=increase",
                            f"crop={W}:{H}:(iw-{W})*{0.5 - ox:.4f}:(ih-{H})*{0.5 - oy:.4f}"])
    else:
        cur = g.chain(cur, [f"scale={W}:{H}"])

    post: list[str] = []
    if include_fps and cfg.fps != "Giữ nguyên":
        try:
            post.append(f"fps={int(cfg.fps)}")
            ctx.fps = float(int(cfg.fps))
        except ValueError:
            pass
    cur = g.chain(cur, post)

    # ── Lớp phủ của các module ───────────────────────────────────────────
    for st in stages or []:
        cur = st(g, cur, ctx)

    tail: list[str] = [f"tpad=stop_mode=clone:stop_duration={extend:.3f}"] if extend > 0.001 else []
    if ass_name:
        tail.append(f"ass={ass_name}")
    tail.append("format=yuv420p")
    g.chain(cur, tail, "[vout]")
    return VideoGraph(graph=g, vout="[vout]", audio_src=asrc, input_args=input_args, ctx=ctx)


def scale_cues(cues: list[Cue], factor: float) -> list[Cue]:
    """Nhân mốc thời gian với ``factor`` (khi video đổi tốc độ)."""
    if abs(factor - 1.0) < 1e-6:
        return cues
    return [Cue(c.index, c.start * factor, c.end * factor, c.text, dict(c.meta)) for c in cues]


def remap_cues_for_trim(cues: list[Cue], trim: TrimSpec | None) -> list[Cue]:
    """Bỏ cue nằm ngoài các đoạn giữ lại và dồn mốc thời gian về liên tục. Câu bị cắt gần hết (còn < 40 % hoặc
    < 0,5 s — vd «Bỏ đầu 3 giây» cắt ngang câu 0–3,2 s) thì bỏ hẳn: chữ vẫn là cả câu, nhét vào mẩu còn lại chỉ làm
    giọng tràn khung và phụ đề nháy. Câu bị cắt một phần còn giữ thì đánh dấu meta «cut»."""
    if trim is None or not trim.active:
        return cues
    out: list[Cue] = []
    offset = 0.0
    for s, e in trim.segments:
        for c in cues:
            cs, ce = max(c.start, s), min(c.end, e)
            if ce - cs <= 0.05:
                continue
            meta = dict(c.meta)
            full = max(0.001, c.end - c.start)
            if ce - cs < full - 0.05:                     # bị cắt một phần
                if (ce - cs) / full < 0.4 or ce - cs < 0.5:
                    continue
                meta["cut"] = True
            out.append(Cue(len(out) + 1, cs - s + offset, ce - s + offset, c.text, meta))
        offset += max(0.0, e - s)
    return out


# ── Âm thanh ──────────────────────────────────────────────────────────────

@dataclass
class AudioExtra:
    """Một track phụ đã đăng ký thành input (nhạc nền…): nhãn + chuỗi filter riêng."""
    label: str                   # vd "[3:a]"
    filters: list[str]           # áp lên track này trước khi mix
    duck: bool = False           # tự hạ khi track chính có tiếng


def voice_timeline_speed(voice: Path | str | None) -> float:
    """Track giọng được dựng theo tốc độ video nào (ghi trong .json đi kèm). Track cũ dựng theo mốc NGUỒN → 1.0:
    khi render / xem trước, giọng cần atempo = tốc độ hiện tại ÷ giá trị này."""
    if not voice:
        return 1.0
    try:
        data = json.loads(Path(voice).with_suffix(".json").read_text(encoding="utf-8"))
        return max(0.25, min(4.0, float(data.get("timeline_speed") or 1.0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0


def orig_swell(cfg: VideoConfig) -> float:
    """Hệ số dâng âm gốc ở chỗ không có giọng (``SWELL``) — 1 = không dâng (âm gốc tắt, đã to, hay tắt tự hạ nền)."""
    level = max(0.0, cfg.original_volume)
    if level <= 0 or not cfg.adaptive_ducking or not cfg.keep_original_audio or cfg.mute:
        return 1.0
    return max(1.0, min(SWELL, SWELL_MAX / level))


def duck_envelope(voice: Path, out: Path, *, duration: float, tempo: float = 1.0, depth_db: float = DUCK_DB,
                  swell: float = 1.0) -> Path | None:
    """Đường bao hạ nhạc nền / âm gốc theo chỗ có giọng (WAV stereo 8 kHz, giá trị mẫu = hệ số nhân, dùng với
    ``amultiply``): hạ từ ``DUCK_ATTACK`` giây trước câu, trả lại trong ``DUCK_RELEASE`` giây sau câu, giữ nguyên mức
    hạ qua khoảng nghỉ < ``DUCK_HOLD`` → nền không nhấp nhô theo từng câu như sidechaincompress.
    ``tempo``: atempo sẽ áp cho giọng khi render (track cũ theo mốc nguồn) — mốc giọng ÷ tempo = mốc video ra.
    ``swell`` > 1: đường bao riêng cho âm gốc đã đặt to lên ×swell — có giọng thì nhân 1/swell (về đúng mức đặt rồi hạ
    như cũ), chỗ không có giọng giữ ×1 (dâng lên); hệ số luôn ≤ 1 nên vẫn ghi được vào WAV."""
    import numpy as np

    from app.services.tts import postprocess

    r = postprocess.decode_any(Path(voice))
    if r is None or duration <= 0:
        return None
    pcm, sr = r
    env = postprocess.envelope(np.frombuffer(pcm, "<i2").astype(np.float32) / 32768, sr)       # khung 10 ms
    if len(env) == 0 or env.max() <= 0:
        return None
    on = env > max(env.max() * 10 ** (-40 / 20), 10 ** (-55 / 20))
    spans: list[list[float]] = []
    i = 0
    while i < len(on):
        if not on[i]:
            i += 1
            continue
        j = i
        while j < len(on) and on[j]:
            j += 1
        s, e = i * postprocess.FRAME / tempo, j * postprocess.FRAME / tempo
        if spans and s - spans[-1][1] < DUCK_HOLD:
            spans[-1][1] = e
        else:
            spans.append([s, e])
        i = j
    rate = 1000                                                    # tính ở 1 ms rồi nội suy lên 8 kHz
    t = np.arange(int(duration * rate) + 1) / rate
    act = np.zeros_like(t)
    for s, e in spans:
        up = np.clip((t - (s - DUCK_ATTACK)) / DUCK_ATTACK, 0, 1)
        down = np.clip(1 - (t - e) / DUCK_RELEASE, 0, 1)
        a = np.minimum(0.5 - 0.5 * np.cos(np.pi * up), 0.5 - 0.5 * np.cos(np.pi * down))
        np.maximum(act, a, out=act)
    gain = 10 ** (depth_db * act / 20)
    if swell > 1.0:
        gain = gain * (1 / swell + (1 - 1 / swell) * (1 - act))
    n8 = int(duration * 8000)
    g8 = (np.clip(np.interp(np.arange(n8) / 8000, t, gain), 0, 32767 / 32768) * 32768).astype("<i2")
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)              # stereo sẵn: ffmpeg nâng mono → stereo nhân 0,707 (−3 dB) vào hệ số
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(np.repeat(g8, 2).tobytes())
    return out


def build_audio_graph(g: Graph, info: MediaInfo, cfg: VideoConfig, *, audio_src: str | None,
                      voice_label: str | None, extras: list[AudioExtra] | None = None,
                      duration: float = 0.0, orig_filters: list[str] | None = None,
                      voice_tempo: float | None = None, duck_label: str | None = None,
                      orig_duck_label: str | None = None, orig_swell: float = 1.0,
                      preview_loudness: float | None = None) -> str | None:
    """Thêm nhánh audio vào ``g``; trả về nhãn ra (hoặc None nếu không có audio). Chưa chuẩn hoá độ to — bản render
    làm 2 lượt riêng ở ``master_audio``.

    ``voice_tempo``: atempo cho giọng (None = theo tốc độ video, track cũ dựng theo mốc nguồn); track mới đã dựng theo
    mốc video ra → 1.0 (không time-stretch lần hai). ``duck_label``: đường bao ``duck_envelope`` (48 kHz stereo) —
    có thì hạ âm gốc / nhạc nền theo đó, không có thì sidechaincompress như trước. ``preview_loudness``: phát xem
    trước — chuẩn hoá nhanh 1 lượt cho gần độ to bản render. ``orig_duck_label`` + ``orig_swell``: đường bao riêng của
    âm gốc (``duck_envelope(swell=)``) — âm gốc đặt to ×swell, đường bao đưa về mức đặt khi có giọng."""
    speed = max(0.25, min(4.0, cfg.speed / 100.0))
    use_orig = bool(audio_src) and cfg.keep_original_audio and not cfg.mute
    use_voice = bool(voice_label) and not cfg.mute
    extras = [] if cfg.mute else list(extras or [])
    if not use_orig and not use_voice and not extras:
        return None

    tracks: list[str] = []
    orig = voice = None
    own = bool(orig_duck_label and use_orig and use_voice and cfg.adaptive_ducking)   # âm gốc có đường bao riêng
    if use_orig:
        f = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
        if cfg.vocal_filter:
            f.append("pan=stereo|c0=0.7*c0-0.7*c1|c1=0.7*c1-0.7*c0")
        if abs(speed - 1.0) > 0.001:
            f.append(atempo_chain(speed))
        f.extend(orig_filters or [])
        f.append(f"volume={max(0.0, cfg.original_volume) * (orig_swell if own else 1.0) / 100:.3f}")
        orig = g.chain(audio_src, f, "[orig]")
    if use_voice:
        vt = speed if voice_tempo is None else voice_tempo
        f = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
        if abs(vt - 1.0) > 0.001:
            f.append(atempo_chain(vt))
        f.append(f"volume={max(0.0, cfg.tts_volume) / 100:.3f}")
        if not use_orig and duration > 0:
            f.append(f"apad=whole_dur={duration:.3f}")
        voice = g.chain(voice_label, f, "[voice]")

    duck_orig = bool(orig and voice and cfg.adaptive_ducking)
    ducked = [i for i, ex in enumerate(extras) if ex.duck] if (orig or voice) else []
    envs: list[str] = []
    if duck_label and voice and ((duck_orig and not own) or ducked):
        n = int(duck_orig and not own) + len(ducked)
        envs = [f"[duk{k}]" for k in range(n)]
        g.add(f"{duck_label}asplit={n}{''.join(envs)}" if n > 1 else f"{duck_label}anull{envs[0]}")

    if orig and voice:
        if own:
            g.add(f"{orig}{orig_duck_label}amultiply[ducked]")
            orig = "[ducked]"
        elif duck_orig and envs:
            g.add(f"{orig}{envs.pop(0)}amultiply[ducked]")
            orig = "[ducked]"
        elif duck_orig:
            # sidechaincompress kết thúc khi nhánh sidechain (giọng) hết → đệm giọng vô hạn để âm gốc không bị cụt
            g.add(f"{voice}asplit=2[voice_a][voice_b0]")
            g.add("[voice_b0]apad[voice_b]")
            g.add(f"{orig}[voice_b]sidechaincompress=threshold=0.035:ratio=6:attack=25:release=900:makeup=1[ducked]")
            orig, voice = "[ducked]", "[voice_a]"
        # duration=longest: giọng dài hơn âm gốc không bị cắt cứng ở cuối
        g.add(f"{orig}{voice}amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mix]")
        main = "[mix]"
    else:
        main = orig or voice

    if ducked and not envs:
        g.add(f"{main}asplit={len(ducked) + 1}[mainA]{''.join(f'[sc{i}]' for i in ducked)}")
        main = "[mainA]"
    for i, ex in enumerate(extras):
        f = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo", *ex.filters]
        lab = g.chain(ex.label, f, f"[extra{i}]")
        if i in ducked:
            if envs:
                g.add(f"{lab}{envs.pop(0)}amultiply[extraD{i}]")
            else:
                g.add(f"[sc{i}]apad[scp{i}]")
                g.add(f"{lab}[scp{i}]sidechaincompress=threshold=0.05:ratio=6:attack=25:release=600:makeup=1[extraD{i}]")
            lab = f"[extraD{i}]"
        tracks.append(lab)

    if tracks:
        if main:
            g.add(f"{main}{''.join(tracks)}amix=inputs={len(tracks) + 1}:duration=longest:dropout_transition=0:normalize=0[mixall]")
        elif len(tracks) == 1:
            g.add(f"{tracks[0]}anull[mixall]")
        else:
            g.add(f"{''.join(tracks)}amix=inputs={len(tracks)}:duration=longest:dropout_transition=0:normalize=0[mixall]")
        main = "[mixall]"

    if preview_loudness is not None and cfg.normalize_audio:
        g.add(f"{main}loudnorm=I={preview_loudness:.1f}:TP=-1.5:LRA=11[aout]")
    else:
        g.add(f"{main}anull[aout]")
    return "[aout]"


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def measure_loudness(ffmpeg: FFmpeg, path: Path) -> dict:
    """Đo độ to (loudnorm print_format=json, chỉ đo): input_i (LUFS), input_tp (dBTP), input_lra…"""
    res = subprocess.run([ffmpeg.ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-vn",
                          "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                         timeout=1800)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr, re.S)
    if not m:
        return {}
    return {k: float(v) for k, v in json.loads(m[-1]).items() if _isnum(v)}


def master_audio(ffmpeg: FFmpeg, src: Path, dst: Path, *, target: float | None, tp: float = MASTER_TP,
                 cancel: threading.Event | None = None) -> dict:
    """Bước cuối phần tiếng, 2 lượt: đo độ to → khuếch đại TUYẾN TÍNH đúng ``target`` LUFS (None = giữ nguyên) →
    giới hạn đỉnh ở 192 kHz (≈ true peak) ≤ ``tp`` dBTP → về 48 kHz bằng soxr (resample đúng một lần).
    Giới hạn đỉnh luôn chạy, kể cả khi tắt chuẩn hoá. Khác loudnorm 1 lượt: không «bơm», câu đầu không to vọt."""
    st = measure_loudness(ffmpeg, src)
    i_in = st.get("input_i")
    gain = target - i_in if target is not None and i_in is not None and i_in > -70 else 0.0
    limit = max(0.0625, min(1.0, 10 ** (tp / 20)))
    out: dict = {}
    for _attempt in range(2):
        chain = (f"volume={gain:.2f}dB,aresample=192000:resampler=soxr,"
                 f"alimiter=limit={limit:.4f}:attack=1:release=60:level=false:latency=true,"
                 "aresample=48000:resampler=soxr")
        ffmpeg.run(["-i", str(src), "-af", chain, "-c:a", "pcm_f32le", str(dst)], cancel=cancel, log_tag="master")
        out = measure_loudness(ffmpeg, dst)
        if target is None or "input_i" not in out or abs(out["input_i"] - target) <= 0.4:
            break
        gain += target - out["input_i"]               # bộ giới hạn đỉnh ăn bớt độ to → bù lại một lần
    info = {"input_i": i_in, "gain_db": round(gain, 2), "output_i": out.get("input_i"), "output_tp": out.get("input_tp")}
    logger.info("Chuẩn hoá âm thanh: %s", info)
    return info


# ── Encoder ───────────────────────────────────────────────────────────────

if sys.platform == "darwin":
    HW_BACKENDS: tuple[str, ...] = ("videotoolbox",)       # chip Apple: bộ mã hóa phần cứng của macOS
else:
    HW_BACKENDS = ("nvenc", "qsv", "amf")     # NVIDIA · Intel (kể cả iGPU) · AMD (kể cả APU)
BACKEND_LABEL = {"nvenc": "NVENC (NVIDIA)", "qsv": "QSV (Intel)", "amf": "AMF (AMD)", "videotoolbox": "VideoToolbox (Apple)"}


def available_encoders(ffmpeg: FFmpeg, codec: str = "h264") -> dict[str, bool]:
    """Thử thật từng bộ mã hóa phần cứng (kết quả được FFmpeg cache) — dùng để báo trong UI."""
    return {b: ffmpeg.encoder_works(f"{codec}_{b}") for b in HW_BACKENDS}


def _option(opts: set[str], *names: str) -> str | None:
    """Tên tuỳ chọn encoder mà bản ffmpeg đang dùng có (ffmpeg 9 đổi ``spatial_aq`` → ``spatial-aq``)."""
    return next((n for n in names if n in opts), None)


def hw_encoder_of(args: list[str]) -> str | None:
    """Bộ mã hoá phần cứng trong tham số ``pick_encoder`` (None = mã hoá bằng CPU)."""
    enc = args[args.index("-c:v") + 1] if "-c:v" in args[:-1] else ""
    return enc if enc.rsplit("_", 1)[-1] in (*HW_BACKENDS, "videotoolbox") else None


def cpu_fallback(ffmpeg: FFmpeg, enc_args: list[str], exc: BaseException,
                 log: Callable[[str], None] | None = None) -> bool:
    """Xuất bằng GPU lỗi (không phải Dừng, không phải ổ đầy / file nguồn hỏng) → đánh dấu bộ mã hoá hỏng cho phiên này,
    trả True để nơi gọi xuất lại bằng CPU thay vì báo lỗi cả video."""
    hw = hw_encoder_of(enc_args)
    if hw is None or isinstance(exc, RenderCancelled) or not isinstance(exc, RuntimeError)             or not cpu_may_help(str(exc)):
        return False
    ffmpeg.mark_encoder_broken(hw)
    if log:
        why = str(exc).splitlines()[0].removeprefix("ffmpeg lỗi: ")[:160]
        log(f"⚠ Bộ mã hoá GPU {hw} lỗi ({why}) → xuất lại bằng CPU, chậm hơn. Phiên này các video sau dùng CPU.")
    return True


#: trần bitrate hình H.264 (Mbps) theo cạnh ngắn của khung ra — đo trên video review 1080×1920 (audit_out/bitrate):
#: trần 6 Mbps giữ VMAF 98,5 so với bản không giới hạn (10–12 Mbps), file nhẹ ~½; TikTok / Reels / Shorts / Facebook
#: nén lại sau khi đăng nên bitrate cao hơn không đẹp hơn
_CAP_H264 = ((720, 3.5), (1080, 6.0), (1440, 10.0), (100000, 20.0))
AUDIO_SOCIAL, AUDIO_MAX = "128k", "192k"


def bitrate_cap(cfg: VideoConfig, width: int, height: int, fps: float = 30.0,
                source: tuple[int, int] | None = None) -> float | None:
    """Trần bitrate hình (Mbps) theo «Dung lượng file xuất»; None = không giới hạn (giữ chất lượng tối đa).
    ``source``: khung video gốc — gốc nhỏ hơn khung ra (576p phóng lên 1080p) thì chi tiết chỉ tới độ phân giải gốc,
    trần tính theo gốc (đo video chó 576×1024 → 1080×1920: 3,5 Mbps VMAF 95,8 so với bản 6 Mbps, 40 so với 71 MB)."""
    mode = getattr(cfg, "size_mode", "social")
    if mode == "max":
        return None
    short = min(width, height) if width and height else 1080
    if source and all(source):
        short = min(short, min(source))
    cap = next(c for s, c in _CAP_H264 if short <= s)
    if fps and fps > 31:
        cap *= 1.5
    if cfg.video_codec == "AV1":
        cap *= 0.45
    elif mode == "small" or cfg.video_codec == VIDEO_CODECS[1]:
        cap *= 0.5                                       # H.265 cùng chất lượng ~½ bitrate (đo: 3 Mbps VMAF 95)
    return round(cap, 2)


def audio_bitrate(cfg: VideoConfig) -> str:
    return AUDIO_MAX if getattr(cfg, "size_mode", "social") == "max" else AUDIO_SOCIAL


def pick_encoder(ffmpeg: FFmpeg, cfg: VideoConfig, log: Callable[[str], None] | None = None,
                 cap: float | None = None) -> list[str]:
    """``cap``: trần bitrate hình (Mbps, ``bitrate_cap``) — None = chỉ theo chất lượng (như cũ)."""
    av1 = cfg.video_codec == "AV1"
    hevc = not av1 and (cfg.video_codec == VIDEO_CODECS[1] or getattr(cfg, "size_mode", "") == "small")
    q = max(1, min(51, int(cfg.quality)))
    preset_idx = ENCODE_PRESETS.index(cfg.encode_preset) if cfg.encode_preset in ENCODE_PRESETS else 1
    # chất lượng theo q nhưng không vượt trần (VBR có trần / CRF có trần); bộ đệm 2 giây
    rate = ["-maxrate", f"{cap:.2f}M", "-bufsize", f"{2 * cap:.2f}M"] if cap else []
    avg = f"{0.8 * cap:.2f}M" if cap else ""

    order: list[str] = []
    if cfg.use_gpu:
        if cfg.gpu_backend == "auto":
            order = list(HW_BACKENDS)
        elif cfg.gpu_backend != "cpu":
            # Chọn cụ thể nhưng máy khác không có → vẫn dò các backend còn lại rồi mới về CPU
            order = [cfg.gpu_backend] + [b for b in HW_BACKENDS if b != cfg.gpu_backend]
    for i, backend in enumerate(order):
        enc = ("av1" if av1 else "hevc" if hevc else "h264") + "_" + backend
        if not ffmpeg.encoder_works(enc):
            if log and i == 0 and cfg.gpu_backend != "auto":
                log(f"⚠ {BACKEND_LABEL.get(backend, backend)} không dùng được trên máy này → tự dò bộ mã hóa khác")
            continue
        if log:
            log(f"Encoder GPU: {enc}")
        opts = ffmpeg.encoder_options(enc)
        if backend == "nvenc":
            p = ["p3", "p5", "p7"][preset_idx]
            args = ["-c:v", enc, "-preset", p, "-tune", "hq", "-rc", "vbr", "-cq", str(q), "-b:v", "0", *rate]
            for names in (("spatial-aq", "spatial_aq"), ("temporal-aq", "temporal_aq")):
                name = _option(opts, *names)
                if name:                                 # bản ffmpeg không có (hay không dò được) → bỏ, không lỗi
                    args += [f"-{name}", "1"]
        elif backend == "qsv":
            p = ["veryfast", "medium", "slower"][preset_idx]
            # ICQ (global_quality) không nhận trần → có trần thì chạy VBR
            args = ["-c:v", enc, "-preset", p, *(["-b:v", avg, *rate] if cap else ["-global_quality", str(q)])]
        elif backend == "videotoolbox":
            # -q:v 1–100 (cao = đẹp): quy đổi gần đúng từ thang CRF — 18 → 65, 23 → 55, 28 → 45; có trần → bitrate
            args = ["-c:v", enc, *(["-b:v", avg] if cap else ["-q:v", str(max(1, min(100, 101 - 2 * q)))]),
                    "-allow_sw", "1"]
            if not hevc:
                args += ["-profile:v", "high"]
        else:
            p = ["speed", "balanced", "quality"][preset_idx]
            if cap:                                      # CQP không nhận trần → VBR đỉnh
                args = ["-c:v", enc, "-quality", p, "-rc", "vbr_peak", "-b:v", avg, *rate]
            else:
                args = ["-c:v", enc, "-quality", p, "-rc", "cqp", f"-{_option(opts, 'qp_i', 'qp-i') or 'qp_i'}", str(q),
                        f"-{_option(opts, 'qp_p', 'qp-p') or 'qp_p'}", str(q)]
        if hevc:
            args += ["-tag:v", "hvc1"]
        return args
    if log:
        log("Encoder CPU: " + ("libx265" if hevc else "libx264"))
    p = ["veryfast", "medium", "slow"][preset_idx]
    if av1:
        return ["-c:v", "libsvtav1", "-preset", ["10", "8", "6"][preset_idx], "-crf", str(min(63, q + 8)), *rate]
    if hevc:
        return ["-c:v", "libx265", "-preset", p, "-crf", str(q), *rate, "-tag:v", "hvc1", "-x265-params",
                "log-level=error"]
    return ["-c:v", "libx264", "-preset", p, "-crf", str(q), *rate, "-profile:v", "high"]


# ── Kế hoạch render ───────────────────────────────────────────────────────

@dataclass
class RenderPlan:
    cmd: list[str]
    cwd: Path
    duration: float
    out_path: Path
    notes: list[str] = field(default_factory=list)
    audio_cmd: list[str] | None = None         # dựng riêng phần tiếng → mix_path (chạy trước lệnh hình)
    mix_path: Path | None = None
    master_path: Path | None = None
    loudness: float | None = None              # LUFS đích; None = giữ độ to (vẫn giới hạn đỉnh)
    master_info: dict = field(default_factory=dict)
    enc_args: list[str] = field(default_factory=list)
    cpu_cmd: list[str] | None = None           # cùng lệnh nhưng mã hoá CPU — GPU lỗi thì xuất lại bằng lệnh này


def _audio_source(g: Graph, info: MediaInfo, trim: TrimSpec | None) -> str | None:
    """Âm nguồn cho lệnh chỉ-có-tiếng: cắt nhiều đoạn → atrim + concat (một đoạn thì đã cắt bằng -ss/-to ở input)."""
    if not info.has_audio:
        return None
    segs = [(max(0.0, s), min(info.duration or e, e)) for s, e in trim.segments if e > s] \
        if trim is not None and trim.active else []
    if len(segs) <= 1:
        return "[0:a]"
    labels = [g.chain("[0:a]", [f"atrim=start={s:.3f}:end={e:.3f}", "asetpts=PTS-STARTPTS"], f"[at{i}]")
              for i, (s, e) in enumerate(segs)]
    g.add(f"{''.join(labels)}concat=n={len(segs)}:v=0:a=1[atcat]")
    return "[atcat]"


def plan_render(item: VideoItem, info: MediaInfo, vcfg: VideoConfig, *, ffmpeg: FFmpeg, job_dir: Path,
                out_path: Path, ass_name: str | None, voice_path: Path | None,
                stages: list[Stage] | None = None, pre_stages: list[Stage] | None = None,
                src_stages: list[Stage] | None = None, trim: TrimSpec | None = None,
                audio_extras: Callable[[Graph, RenderContext], list[AudioExtra]] | None = None,
                audio_override: Path | None = None, voice_duration: float = 0.0,
                audio_only: bool = False, audio_orig_filters: list[str] | None = None,
                log: Callable[[str], None] | None = None, voice_tempo: float | None = None,
                loudness: float | None = None) -> RenderPlan:
    """``audio_override``: file audio thay cho tiếng gốc (vd âm gốc đã khử giọng nói bằng AI).
    ``voice_duration``: độ dài giọng theo mốc video RA (sau ``voice_tempo``); + ``vcfg.loop_to_voice``: lặp video cho
    đủ giọng, không thì giọng dài hơn video → giữ khung cuối (không cắt giọng).

    Phần tiếng dựng bằng lệnh riêng (không giải mã hình) → mix.wav → ``master_audio`` 2 lượt (``loudness`` LUFS,
    giới hạn đỉnh) → master.wav, rồi ghép thẳng vào hình. Hình và tiếng cắt đúng một độ dài (số khung ÷ fps)."""
    job_dir.mkdir(parents=True, exist_ok=True)
    speed = max(0.25, min(4.0, vcfg.speed / 100.0))
    src_len = trim.kept_duration if trim is not None and trim.active else info.duration
    loop_video = bool(vcfg.loop_to_voice and voice_path is not None and voice_duration > 0
                      and (trim is None or not trim.active))
    looped = loop_video and voice_duration * speed > info.duration
    eff_info = info
    if looped:
        from dataclasses import replace as _replace
        eff_info = _replace(info, duration=voice_duration * speed + 0.5)
    extend = 0.0
    if voice_path is not None and not loop_video and src_len > 0 and voice_duration > src_len / speed + 0.05:
        extend = voice_duration - src_len / speed + 0.3
        if log:
            log(f"⚠ Giọng dài hơn video {voice_duration - src_len / speed:.1f}s → giữ khung cuối thêm {extend:.1f}s "
                "(không cắt giọng). Nên rút gọn bản dịch.")
    vg = build_video_graph(eff_info, vcfg, work_dir=job_dir, ass_name=ass_name, stages=stages,
                           pre_stages=pre_stages, src_stages=src_stages, trim=trim, first_extra_input=1,
                           extend=extend)
    dur = vg.ctx.duration
    if vg.ctx.fps > 0:
        dur = max(1.0 / vg.ctx.fps, round(dur * vg.ctx.fps) / vg.ctx.fps)     # đúng số khung → tiếng = hình
    in_args = [*vg.input_args] + (["-stream_loop", "-1"] if looped else [])

    # ── Phần tiếng: lệnh riêng ────────────────────────────────────────────
    ga = Graph(1)
    a_src = _audio_source(ga, info, trim)
    if audio_override is not None and audio_override.exists():
        a_src = f"[{ga.add_input(['-i', str(audio_override)])}:a]"
    voice_label = f"[{ga.add_input(['-i', str(voice_path)])}:a]" if voice_path is not None else None
    extras = audio_extras(ga, vg.ctx) if audio_extras else []
    duck = duck_o = None
    duck_orig = vcfg.adaptive_ducking and vcfg.keep_original_audio and a_src is not None
    wants_duck = voice_path is not None and not vcfg.mute and (duck_orig or any(ex.duck for ex in extras))
    swell = orig_swell(vcfg) if duck_orig and voice_path is not None else 1.0
    if wants_duck:
        vt = speed if voice_tempo is None else voice_tempo
        if any(ex.duck for ex in extras) or swell <= 1.0:
            env = duck_envelope(voice_path, job_dir / "duck.wav", duration=dur, tempo=vt)
            if env is not None:
                duck = ga.chain(f"[{ga.add_input(['-i', str(env)])}:a]",
                                ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"], "[duckenv]")
        if swell > 1.0:                               # âm gốc: dâng ở chỗ không có giọng
            env = duck_envelope(voice_path, job_dir / "duck_goc.wav", duration=dur, tempo=vt, swell=swell)
            if env is not None:
                duck_o = ga.chain(f"[{ga.add_input(['-i', str(env)])}:a]",
                                  ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"], "[duckgoc]")
    mix = build_audio_graph(ga, eff_info if looped else info, vcfg, audio_src=a_src, voice_label=voice_label,
                            extras=extras, duration=dur, orig_filters=audio_orig_filters, voice_tempo=voice_tempo,
                            duck_label=duck, orig_duck_label=duck_o, orig_swell=swell)
    audio_cmd = mix_path = master_path = None
    if mix:
        ga.add(f"{mix}apad=whole_dur={dur:.3f},atrim=end={dur:.3f},asetpts=PTS-STARTPTS[am]")
        a_script = job_dir / "filters_audio.txt"
        a_script.write_text(ga.text(), encoding="utf-8")
        mix_path, master_path = job_dir / "mix.wav", job_dir / "master.wav"
        audio_cmd = [*in_args, "-i", item.path]
        for extra in ga.extra_inputs:
            audio_cmd += extra
        audio_cmd += ["-/filter_complex", a_script.name, "-map", "[am]", "-c:a", "pcm_f32le", "-ar", "48000",
                      str(mix_path)]
    target = loudness if vcfg.normalize_audio else None
    if audio_only:
        if not audio_cmd:
            raise RuntimeError("Video không có âm thanh để xuất MP3.")
        cmd = ["-i", str(master_path), "-c:a", "libmp3lame", "-q:a", "2", "-t", f"{dur:.3f}", str(out_path)]
        return RenderPlan(cmd=cmd, cwd=job_dir, duration=dur, out_path=out_path, audio_cmd=audio_cmd,
                          mix_path=mix_path, master_path=master_path, loudness=target)

    # ── Hình (+ ghép tiếng đã master) ─────────────────────────────────────
    g = vg.graph
    if vg.audio_src and vg.audio_src != "[0:a]":
        g.add(f"{vg.audio_src}anullsink")          # nhánh tiếng của concat (cắt nhiều đoạn) không dùng ở lệnh này
    m_idx = g.add_input(["-i", str(master_path)]) if audio_cmd else None
    cmd = [*in_args, "-i", item.path]
    for extra in g.extra_inputs:
        cmd += extra
    script = job_dir / "filters.txt"
    script.write_text(g.text(), encoding="utf-8")
    # FFmpeg ≥ 7: "-/option file" đọc giá trị option từ file (thay cho -filter_complex_script đã bỏ)
    cmd += ["-/filter_complex", script.name, "-map", vg.vout]
    if m_idx is not None:
        cmd += ["-map", f"{m_idx}:a", "-c:a", "aac", "-b:a", audio_bitrate(vcfg), "-ar", "48000"]
    else:
        cmd += ["-an"]
    tail = ["-g", str(int(vcfg.gop_size)), "-keyint_min", str(max(1, int(vcfg.gop_size) // 2))]         if vcfg.gop_size and vcfg.gop_size > 0 else []
    tail += ["-t", f"{dur:.3f}", "-movflags", "+faststart", str(out_path)]
    fps_out = float(vcfg.fps) if str(vcfg.fps).isdigit() else (info.fps or 30.0)
    src = (info.width, info.height)
    cap = bitrate_cap(vcfg, vg.ctx.width, vg.ctx.height, fps_out, source=src)        # «Dung lượng file xuất»
    enc = pick_encoder(ffmpeg, vcfg, log, cap=cap)
    if log and cap:
        low = all(src) and min(src) < min(vg.ctx.width, vg.ctx.height)
        log(f"Dung lượng file: trần hình {cap:g} Mbps · tiếng {audio_bitrate(vcfg)} "
            f"({'nhẹ nhất, H.265' if vcfg.size_mode == 'small' else 'đăng mạng xã hội'})"
            + (f" — theo video gốc {min(src)}p" if low else ""))
    cpu_cmd = None
    if hw_encoder_of(enc):
        from dataclasses import replace as _replace
        cpu_cmd = cmd + pick_encoder(ffmpeg, _replace(vcfg, use_gpu=False), cap=cap) + tail
    return RenderPlan(cmd=cmd + enc + tail, cwd=job_dir, duration=dur, out_path=out_path, audio_cmd=audio_cmd,
                      mix_path=mix_path, master_path=master_path, loudness=target, enc_args=enc, cpu_cmd=cpu_cmd)


def run_plan(plan: RenderPlan, ffmpeg: FFmpeg, *, on_progress: Callable[[float], None] | None = None,
             cancel: threading.Event | None = None, log: Callable[[str], None] | None = None) -> Path:
    v_prog = on_progress
    if plan.audio_cmd:
        ffmpeg.run(plan.audio_cmd, duration=plan.duration, on_progress=(lambda p: on_progress(0.15 * p))
                   if on_progress else None, cancel=cancel, cwd=plan.cwd, log_tag="audio")
        plan.master_info = master_audio(ffmpeg, plan.mix_path, plan.master_path, target=plan.loudness, cancel=cancel)
        if on_progress:
            on_progress(0.2)
            v_prog = lambda p: on_progress(0.2 + 0.8 * p)      # noqa: E731
    # ghi ra file tạm rồi mới đổi tên: Dừng / lỗi giữa chừng không để lại video dở (hay đè mất bản xuất cũ)
    out = plan.out_path
    tmp = out.with_name(f"{out.stem}.dang-xuat{out.suffix}")
    if not (plan.cmd and plan.cmd[-1] == str(out)):
        tmp = out

    def _to_tmp(c: list[str]) -> list[str]:
        return c[:-1] + [str(tmp)] if tmp != out else list(c)

    try:
        try:
            ffmpeg.run(_to_tmp(plan.cmd), duration=plan.duration, on_progress=v_prog, cancel=cancel, cwd=plan.cwd,
                       log_tag="render")
        except Exception as exc:
            if plan.cpu_cmd is None or not cpu_fallback(ffmpeg, plan.enc_args, exc, log):
                raise
            ffmpeg.run(_to_tmp(plan.cpu_cmd), duration=plan.duration, on_progress=v_prog, cancel=cancel,
                       cwd=plan.cwd, log_tag="render-cpu")
    except BaseException:
        if tmp != out:
            tmp.unlink(missing_ok=True)
        raise
    if tmp != out:
        try:
            tmp.replace(out)
        except PermissionError:
            raise RuntimeError(f"Không ghi đè được {out.name} (file đang mở ở trình phát khác?) — bản mới để ở "
                               f"{tmp.name}. Đóng trình phát rồi Thử lại.") from None
    return out
