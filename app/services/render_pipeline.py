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

import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.state import (ENCODE_PRESETS, FIT_MODES, RATIO_VALUES, RESOLUTIONS, VIDEO_CODECS, VideoConfig,
                            VideoItem)
from app.services.ffmpeg import FFmpeg, MediaInfo, atempo_chain
from app.services.srt_utils import Cue

logger = logging.getLogger(__name__)

_RATIO_VALUE = {k: v for k, v in RATIO_VALUES.items() if v}
_RES_SHORT = {RESOLUTIONS[1]: 720, RESOLUTIONS[2]: 1080, RESOLUTIONS[3]: 1440, RESOLUTIONS[4]: 2160}


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
    return (W - dw) / 2 / W, (H - dh) / 2 / H, dw / W, dh / H


def source_to_output(info: MediaInfo, cfg: VideoConfig, x: float, y: float) -> tuple[float, float]:
    """Điểm (x, y) theo tỉ lệ khung NGUỒN → tỉ lệ khung RA (qua lật gương, phóng to, đổi khung)."""
    zoom = max(1.0, min(4.0, cfg.zoom / 100.0))
    if cfg.flip_h:
        x = 1.0 - x
    x, y = (x - 0.5) * zoom + 0.5, (y - 0.5) * zoom + 0.5
    ox, oy, sx, sy = source_rect_in_output(info, cfg)
    return ox + x * sx, oy + y * sy


def build_video_graph(info: MediaInfo, cfg: VideoConfig, *, work_dir: Path, ass_name: str | None = None,
                      stages: list[Stage] | None = None, pre_stages: list[Stage] | None = None,
                      src_stages: list[Stage] | None = None,
                      trim: TrimSpec | None = None, include_fps: bool = True, t0: float = 0.0,
                      preview: bool = False, first_extra_input: int = 1, preview_scale: float = 1.0) -> VideoGraph:
    """Dựng toàn bộ nhánh video. ``src_stages`` áp thẳng lên khung nguồn (che mờ — toạ độ theo video gốc, đi theo
    mọi kiểu khung ra); ``pre_stages`` chạy sau lật/phóng nhưng trước khi đổi khung (màu, hiệu ứng).

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

    ctx = RenderContext(width=geo.width, height=geo.height, fps=info.fps or 30.0,
                        duration=src_dur / speed if speed else src_dur, work_dir=work_dir, t0=t0,
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
        pre.append(f"crop=iw/{zoom:.4f}:ih/{zoom:.4f}")
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
        g.add(f"{bg}{fg}overlay=(W-w)/2:(H-h)/2{cur}")
    elif geo.mode == "pad":
        cur = g.chain(cur, [f"scale={W}:{H}:force_original_aspect_ratio=decrease",
                            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"])
    elif geo.mode == "crop":
        cur = g.chain(cur, [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"])
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

    tail: list[str] = []
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
    """Bỏ cue nằm ngoài các đoạn giữ lại và dồn mốc thời gian về liên tục."""
    if trim is None or not trim.active:
        return cues
    out: list[Cue] = []
    offset = 0.0
    for s, e in trim.segments:
        for c in cues:
            cs, ce = max(c.start, s), min(c.end, e)
            if ce - cs <= 0.05:
                continue
            out.append(Cue(len(out) + 1, cs - s + offset, ce - s + offset, c.text, dict(c.meta)))
        offset += max(0.0, e - s)
    return out


# ── Âm thanh ──────────────────────────────────────────────────────────────

@dataclass
class AudioExtra:
    """Một track phụ đã đăng ký thành input (nhạc nền…): nhãn + chuỗi filter riêng."""
    label: str                   # vd "[3:a]"
    filters: list[str]           # áp lên track này trước khi mix
    duck: bool = False           # tự hạ khi track chính có tiếng


def build_audio_graph(g: Graph, info: MediaInfo, cfg: VideoConfig, *, audio_src: str | None,
                      voice_label: str | None, extras: list[AudioExtra] | None = None,
                      duration: float = 0.0, orig_filters: list[str] | None = None) -> str | None:
    """Thêm nhánh audio vào ``g``; trả về nhãn ra (hoặc None nếu không có audio)."""
    speed = max(0.25, min(4.0, cfg.speed / 100.0))
    use_orig = bool(audio_src) and cfg.keep_original_audio and not cfg.mute
    use_voice = bool(voice_label) and not cfg.mute
    extras = [] if cfg.mute else list(extras or [])
    if not use_orig and not use_voice and not extras:
        return None

    tracks: list[str] = []
    orig = voice = None
    if use_orig:
        f = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
        if cfg.vocal_filter:
            f.append("pan=stereo|c0=0.7*c0-0.7*c1|c1=0.7*c1-0.7*c0")
        if abs(speed - 1.0) > 0.001:
            f.append(atempo_chain(speed))
        f.extend(orig_filters or [])
        f.append(f"volume={max(0.0, cfg.original_volume) / 100:.3f}")
        orig = g.chain(audio_src, f, "[orig]")
    if use_voice:
        f = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
        if abs(speed - 1.0) > 0.001:
            f.append(atempo_chain(speed))
        f.append(f"volume={max(0.0, cfg.tts_volume) / 100:.3f}")
        if not use_orig and duration > 0:
            f.append(f"apad=whole_dur={duration:.3f}")
        voice = g.chain(voice_label, f, "[voice]")

    if orig and voice:
        if cfg.adaptive_ducking:
            # sidechaincompress kết thúc khi nhánh sidechain (giọng) hết → đệm giọng vô hạn để âm gốc không bị cụt
            g.add(f"{voice}asplit=2[voice_a][voice_b0]")
            g.add("[voice_b0]apad[voice_b]")
            g.add(f"{orig}[voice_b]sidechaincompress=threshold=0.035:ratio=6:attack=25:release=900:makeup=1[ducked]")
            g.add("[ducked][voice_a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        else:
            g.add(f"{orig}{voice}amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        main = "[mix]"
    else:
        main = orig or voice

    ducked = [i for i, ex in enumerate(extras) if ex.duck] if main else []
    if ducked:
        g.add(f"{main}asplit={len(ducked) + 1}[mainA]{''.join(f'[sc{i}]' for i in ducked)}")
        main = "[mainA]"
    for i, ex in enumerate(extras):
        f = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo", *ex.filters]
        lab = g.chain(ex.label, f, f"[extra{i}]")
        if i in ducked:
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

    if cfg.normalize_audio:
        g.add(f"{main}loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    else:
        g.add(f"{main}anull[aout]")
    return "[aout]"


# ── Encoder ───────────────────────────────────────────────────────────────

if sys.platform == "darwin":
    HW_BACKENDS: tuple[str, ...] = ("videotoolbox",)       # Apple Silicon / Mac Intel: bộ mã hóa phần cứng của macOS
else:
    HW_BACKENDS = ("nvenc", "qsv", "amf")     # NVIDIA · Intel (kể cả iGPU) · AMD (kể cả APU)
BACKEND_LABEL = {"nvenc": "NVENC (NVIDIA)", "qsv": "QSV (Intel)", "amf": "AMF (AMD)", "videotoolbox": "VideoToolbox (Apple)"}


def available_encoders(ffmpeg: FFmpeg, codec: str = "h264") -> dict[str, bool]:
    """Thử thật từng bộ mã hóa phần cứng (kết quả được FFmpeg cache) — dùng để báo trong UI."""
    return {b: ffmpeg.encoder_works(f"{codec}_{b}") for b in HW_BACKENDS}


def pick_encoder(ffmpeg: FFmpeg, cfg: VideoConfig, log: Callable[[str], None] | None = None) -> list[str]:
    hevc = cfg.video_codec == VIDEO_CODECS[1]
    av1 = cfg.video_codec == "AV1"
    q = max(1, min(51, int(cfg.quality)))
    preset_idx = ENCODE_PRESETS.index(cfg.encode_preset) if cfg.encode_preset in ENCODE_PRESETS else 1

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
        if backend == "nvenc":
            p = ["p3", "p5", "p7"][preset_idx]
            args = ["-c:v", enc, "-preset", p, "-tune", "hq", "-rc", "vbr", "-cq", str(q), "-b:v", "0",
                    "-spatial_aq", "1", "-temporal_aq", "1"]
        elif backend == "qsv":
            p = ["veryfast", "medium", "slower"][preset_idx]
            args = ["-c:v", enc, "-preset", p, "-global_quality", str(q)]
        elif backend == "videotoolbox":
            # -q:v 1–100 (cao = đẹp): quy đổi gần đúng từ thang CRF — 18 → 65, 23 → 55, 28 → 45
            args = ["-c:v", enc, "-q:v", str(max(1, min(100, 101 - 2 * q))), "-allow_sw", "1"]
            if not hevc:
                args += ["-profile:v", "high"]
        else:
            p = ["speed", "balanced", "quality"][preset_idx]
            args = ["-c:v", enc, "-quality", p, "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q)]
        if hevc:
            args += ["-tag:v", "hvc1"]
        return args
    if log:
        log("Encoder CPU: " + ("libx265" if hevc else "libx264"))
    p = ["veryfast", "medium", "slow"][preset_idx]
    if av1:
        return ["-c:v", "libsvtav1", "-preset", ["10", "8", "6"][preset_idx], "-crf", str(min(63, q + 8))]
    if hevc:
        return ["-c:v", "libx265", "-preset", p, "-crf", str(q), "-tag:v", "hvc1", "-x265-params", "log-level=error"]
    return ["-c:v", "libx264", "-preset", p, "-crf", str(q), "-profile:v", "high"]


# ── Kế hoạch render ───────────────────────────────────────────────────────

@dataclass
class RenderPlan:
    cmd: list[str]
    cwd: Path
    duration: float
    out_path: Path
    notes: list[str] = field(default_factory=list)


def plan_render(item: VideoItem, info: MediaInfo, vcfg: VideoConfig, *, ffmpeg: FFmpeg, job_dir: Path,
                out_path: Path, ass_name: str | None, voice_path: Path | None,
                stages: list[Stage] | None = None, pre_stages: list[Stage] | None = None,
                src_stages: list[Stage] | None = None, trim: TrimSpec | None = None,
                audio_extras: Callable[[Graph, RenderContext], list[AudioExtra]] | None = None,
                audio_override: Path | None = None, voice_duration: float = 0.0,
                audio_only: bool = False, audio_orig_filters: list[str] | None = None,
                log: Callable[[str], None] | None = None) -> RenderPlan:
    """``audio_override``: file audio thay cho tiếng gốc (vd âm gốc đã khử giọng nói bằng AI).
    ``voice_duration`` + ``vcfg.loop_to_voice``: lặp video cho đủ thời lượng giọng đọc."""
    job_dir.mkdir(parents=True, exist_ok=True)
    loop_video = bool(vcfg.loop_to_voice and voice_path is not None and voice_duration > 0
                      and (trim is None or not trim.active))
    eff_info = info
    if loop_video and voice_duration > info.duration:
        from dataclasses import replace as _replace
        eff_info = _replace(info, duration=voice_duration + 0.5)
    vg = build_video_graph(eff_info, vcfg, work_dir=job_dir, ass_name=ass_name, stages=stages,
                           pre_stages=pre_stages, src_stages=src_stages, trim=trim, first_extra_input=1)
    g = vg.graph
    voice_label = None
    if voice_path is not None:
        voice_label = f"[{g.add_input(['-i', str(voice_path)])}:a]"
    audio_src = vg.audio_src
    if audio_override is not None and audio_override.exists():
        audio_src = f"[{g.add_input(['-i', str(audio_override)])}:a]"
    extras = audio_extras(g, vg.ctx) if audio_extras else []
    aout = build_audio_graph(g, info if not loop_video else eff_info, vcfg, audio_src=audio_src,
                             voice_label=voice_label, extras=extras, duration=vg.ctx.duration,
                             orig_filters=audio_orig_filters)

    cmd: list[str] = [*vg.input_args]
    if loop_video and voice_duration > info.duration:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", item.path]
    for extra in g.extra_inputs:
        cmd += extra

    script = job_dir / "filters.txt"
    script.write_text(g.text(), encoding="utf-8")
    # FFmpeg ≥ 7: "-/option file" đọc giá trị option từ file (thay cho -filter_complex_script đã bỏ)
    if audio_only:
        if not aout:
            raise RuntimeError("Video không có âm thanh để xuất MP3.")
        # nhánh video không dùng tới → đổ vào nullsink để filtergraph hợp lệ
        g.add(f"{vg.vout}nullsink")
        script.write_text(g.text(), encoding="utf-8")
        cmd += ["-/filter_complex", script.name, "-map", aout, "-vn", "-c:a", "libmp3lame", "-q:a", "2"]
        if loop_video and voice_duration > info.duration:
            cmd += ["-t", f"{vg.ctx.duration:.3f}"]
        cmd += [str(out_path)]
        return RenderPlan(cmd=cmd, cwd=job_dir, duration=vg.ctx.duration, out_path=out_path)
    cmd += ["-/filter_complex", script.name, "-map", vg.vout]
    if aout:
        cmd += ["-map", aout, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        cmd += ["-an"]
    cmd += pick_encoder(ffmpeg, vcfg, log)
    if vcfg.gop_size and vcfg.gop_size > 0:
        cmd += ["-g", str(int(vcfg.gop_size)), "-keyint_min", str(max(1, int(vcfg.gop_size) // 2))]
    if loop_video and voice_duration > info.duration:
        cmd += ["-t", f"{vg.ctx.duration:.3f}"]
    cmd += ["-movflags", "+faststart", str(out_path)]
    return RenderPlan(cmd=cmd, cwd=job_dir, duration=vg.ctx.duration, out_path=out_path)


def run_plan(plan: RenderPlan, ffmpeg: FFmpeg, *, on_progress: Callable[[float], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    ffmpeg.run(plan.cmd, duration=plan.duration, on_progress=on_progress, cancel=cancel, cwd=plan.cwd,
               log_tag="render")
    return plan.out_path
