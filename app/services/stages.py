"""Các "stage" filter của từng module (Blur, Brand, Màu sắc, Cắt, Nhạc nền) cắm vào pipeline.

Mọi biểu thức thời gian dùng ``ctx.t()`` để xem trước đúng mốc; toạ độ vùng/logo tính
theo khung ra (``ctx.width`` × ``ctx.height``). Mốc thời gian người dùng nhập theo
video gốc nên chia cho ``ctx.speed`` khi video đổi tốc độ.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.core.state import (BLUR_STYLES, KENBURNS, MOTIONS, SAFE_ZONES, AppState, BGMConfig, BlurConfig,
                            BrandConfig, ColorConfig, TrimConfig, VideoItem)
from app.services.ffmpeg import MediaInfo, ffescape_path, parse_hhmmss
from app.services.render_pipeline import AudioExtra, Graph, RenderContext, Stage, TrimSpec
from app.services.system_fonts import font_file

logger = logging.getLogger(__name__)


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


# ── Vị trí & chuyển động (dùng chung cho logo / chữ / watermark) ─────────

def _margins(cfg: BrandConfig, W: int, H: int) -> tuple[int, int, int, int]:
    m = max(0, int(cfg.safe_margin))
    l, t, r, b = m, m, m, m
    if cfg.safe_zone == SAFE_ZONES[1]:
        r += int(W * 0.14)
        b += int(H * 0.18)
        t += int(H * 0.06)
    return l, t, r, b


def _anchor(position: str, W: int, H: int, ew: str, eh: str, margins: tuple[int, int, int, int]) -> tuple[str, str]:
    l, t, r, b = margins
    pos = position.lower()
    if pos.startswith("trên"):
        y = str(t)
    elif pos.startswith("dưới"):
        y = f"{H}-{eh}-{b}"
    else:
        y = f"({H}-{eh})/2"
    if pos.endswith("trái"):
        x = str(l)
    elif pos.endswith("phải"):
        x = f"{W}-{ew}-{r}"
    else:
        x = f"({W}-{ew})/2"
    return x, y


def motion_exprs(motion: str, position: str, *, W: int, H: int, ew: str, eh: str, speed: float, delay: float,
                 ctx: RenderContext, margins: tuple[int, int, int, int]) -> tuple[str, str, str]:
    """Trả về (x, y, enable) cho overlay/drawtext."""
    T = ctx.t()
    d = max(0.0, delay) / max(0.25, ctx.speed)
    Td = f"max(0,{T}-{_f(d)})"
    v = max(1.0, speed)
    x0, y0 = _anchor(position, W, H, ew, eh, margins)
    enable = f"gte({T},{_f(d)})" if d > 0 else ""
    if motion == MOTIONS[1]:      # trôi ngang
        x, y = f"mod({_f(v)}*{Td},{W}+{ew})-{ew}", y0
    elif motion == MOTIONS[2]:    # trôi dọc
        x, y = x0, f"mod({_f(v)}*{Td},{H}+{eh})-{eh}"
    elif motion == MOTIONS[3]:    # nảy
        x = f"abs(mod({_f(v)}*{Td},2*({W}-{ew}))-({W}-{ew}))"
        y = f"abs(mod({_f(v * 0.73)}*{Td},2*({H}-{eh}))-({H}-{eh}))"
    elif motion == MOTIONS[4]:    # đường chéo
        x = f"mod({_f(v)}*{Td},{W}+{ew})-{ew}"
        y = f"mod({_f(v * 0.6)}*{Td},{H}+{eh})-{eh}"
    elif motion == MOTIONS[5]:    # nhấp nháy
        x, y = x0, y0
        blink = f"lt(mod({T},1.6),1.0)"
        enable = f"{enable}*{blink}" if enable else blink
    else:
        x, y = x0, y0
    return x, y, enable


# ── Blur vùng ─────────────────────────────────────────────────────────────

def _mask_png(work_dir: Path, w: int, h: int, feather_pct: float, opacity_pct: float, rounded_pct: float = 0.0) -> Path:
    fpx = int(min(w, h) * 0.5 * max(0.0, min(100.0, feather_pct)) / 100)
    op = max(0.0, min(100.0, opacity_pct))
    radius = int(min(w, h) * 0.5 * max(0.0, min(100.0, rounded_pct)) / 100)
    name = f"mask_{w}x{h}_{fpx}_{int(op)}_{radius}.png"
    p = work_dir / name
    if p.exists():
        return p
    work_dir.mkdir(parents=True, exist_ok=True)
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    inset = fpx
    box = [inset, inset, w - 1 - inset, h - 1 - inset]
    if radius > 0:
        d.rounded_rectangle(box, radius=max(1, radius - inset), fill=int(255 * op / 100))
    else:
        d.rectangle(box, fill=int(255 * op / 100))
    if fpx > 0:
        m = m.filter(ImageFilter.GaussianBlur(max(1, fpx / 2)))
    m.save(p)
    return p


def blur_stage(cfg: BlurConfig) -> Stage:
    def stage(g: Graph, cur: str, ctx: RenderContext) -> str:
        if not cfg.enable:
            return cur
        # toạ độ vùng che tính theo KHUNG NGUỒN → phụ đề cứng luôn được che dù xuất 16:9 hay 9:16
        W, H = (ctx.src_width or ctx.width), (ctx.src_height or ctx.height)
        for r in cfg.regions:
            try:
                rw, rh = float(r.get("w", 0)), float(r.get("h", 0))
            except (TypeError, ValueError):
                continue
            if rw < 0.005 or rh < 0.005:
                continue
            x = max(0, min(W - 2, int(float(r.get("x", 0)) * W)))
            y = max(0, min(H - 2, int(float(r.get("y", 0)) * H)))
            w = max(2, min(W - x, int(rw * W)))
            h = max(2, min(H - y, int(rh * H)))
            w -= w % 2
            h -= h % 2
            strength = float(r.get("strength", 60))
            feather = float(r.get("feather", 25))
            opacity = float(r.get("opacity", 100))
            style = r.get("style", BLUR_STYLES[0])
            start = float(r.get("start", 0) or 0)      # giây theo nguồn (stage chạy trước setpts)
            end = float(r.get("end", 0) or 0)

            base, reg = g.label("bb"), g.label("br")
            g.add(f"{cur}split=2{base}{reg}")
            f = [f"crop={w}:{h}:{x}:{y}"]
            if style == BLUR_STYLES[1]:
                p = max(1.5, (4 + strength * 0.36) * ctx.px)
                f += [f"scale=iw/{p:.2f}:ih/{p:.2f}:flags=area", f"scale={w}:{h}:flags=neighbor"]
            elif style == BLUR_STYLES[2]:
                f += ["drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill"]
            else:
                f += [f"gblur=sigma={(1 + strength * 0.5) * ctx.px:.1f}:steps=2"]
            blurred = g.chain(reg, f)
            rounded = float(r.get("rounded", 0) or 0)
            if feather > 0 or opacity < 100 or rounded > 0:
                mask = _mask_png(ctx.work_dir, w, h, feather, opacity, rounded)
                idx = g.add_input(["-i", str(mask)])
                merged = g.label("bm")
                g.add(f"{blurred}[{idx}:v]alphamerge{merged}")
                blurred = merged
            enable = ""
            T = ctx.t_src()
            if end > start > 0 or (end > 0 and start == 0):
                enable = f":enable='between({T},{_f(start)},{_f(end)})'"
            elif start > 0:
                enable = f":enable='gte({T},{_f(start)})'"
            out = g.label("bo")
            g.add(f"{base}{blurred}overlay={x}:{y}{enable}{out}")
            cur = out
        return cur
    return stage


# ── Logo / chữ / watermark ───────────────────────────────────────────────

def _write_text(work_dir: Path, key: str, text: str) -> str:
    work_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    name = f"text_{key}_{h}.txt"
    p = work_dir / name
    if not p.exists():
        p.write_text(text, encoding="utf-8")
    return name


def _drawtext(ctx: RenderContext, *, key: str, text: str, font: str, size: int, color: str, opacity: float,
              box: bool, motion: str, position: str, speed: float, delay: float, margins, shadow: bool) -> str:
    scale = min(ctx.width, ctx.height) / 1080.0
    fs = max(8, int(size * scale))
    ff = font_file(font)
    fontfile = f":fontfile={ffescape_path(ff)}" if ff else ""
    x, y, enable = motion_exprs(motion, position, W=ctx.width, H=ctx.height, ew="text_w", eh="text_h",
                                speed=speed * scale, delay=delay, ctx=ctx, margins=margins)
    alpha = max(0.0, min(1.0, opacity / 100))
    parts = [f"drawtext=textfile={_write_text(ctx.work_dir, key, text)}", "expansion=none", f"fontsize={fs}",
             f"fontcolor={color}@{alpha:.2f}", f"x='{x}'", f"y='{y}'"]
    if fontfile:
        parts.append(fontfile.lstrip(":"))
    if box:
        parts += ["box=1", f"boxcolor=black@{0.45 * alpha:.2f}", f"boxborderw={max(4, int(fs * 0.3))}"]
    elif shadow:
        parts += [f"shadowcolor=black@{0.5 * alpha:.2f}", "shadowx=2", "shadowy=2"]
    else:
        parts += [f"borderw={max(1, int(fs * 0.05))}", f"bordercolor=black@{0.6 * alpha:.2f}"]
    if enable:
        parts.append(f"enable='{enable}'")
    return ":".join(parts)


def brand_stage(cfg: BrandConfig) -> Stage:
    def stage(g: Graph, cur: str, ctx: RenderContext) -> str:
        W, H = ctx.width, ctx.height
        margins = _margins(cfg, W, H)
        if cfg.logo_enable and cfg.logo_path and Path(cfg.logo_path).exists():
            idx = g.add_input(["-i", cfg.logo_path])
            lw = max(8, int(W * max(1.0, min(100.0, cfg.logo_size)) / 100))
            alpha = max(0.0, min(1.0, cfg.logo_opacity / 100))
            logo = g.chain(f"[{idx}:v]", [f"scale={lw}:-1:flags=lanczos", "format=rgba",
                                          f"colorchannelmixer=aa={alpha:.2f}" if alpha < 0.999 else ""], "[logo]")
            x, y, enable = motion_exprs(cfg.logo_motion, cfg.logo_position, W=W, H=H, ew="overlay_w",
                                        eh="overlay_h", speed=cfg.logo_motion_speed, delay=cfg.logo_delay, ctx=ctx,
                                        margins=margins)
            out = g.label("lg")
            en = f":enable='{enable}'" if enable else ""
            g.add(f"{cur}{logo}overlay=x='{x}':y='{y}':eval=frame{en}{out}")
            cur = out
        if cfg.frame_enable and cfg.frame_width > 0:
            alpha = max(0.0, min(1.0, cfg.frame_opacity / 100))
            bw = max(1, int(cfg.frame_width * min(W, H) / 1080))
            cur = g.chain(cur, [f"drawbox=x=0:y=0:w=iw:h=ih:color={cfg.frame_color}@{alpha:.2f}:t={bw}"])
        if cfg.text_enable and cfg.text.strip():
            cur = g.chain(cur, [_drawtext(ctx, key="text", text=cfg.text, font=cfg.text_font, size=cfg.text_size,
                                          color=cfg.text_color, opacity=cfg.text_opacity, box=cfg.text_bg,
                                          motion=cfg.text_motion, position=cfg.text_position,
                                          speed=cfg.text_motion_speed, delay=cfg.text_delay, margins=margins,
                                          shadow=False)])
        if cfg.watermark_enable and cfg.watermark_text.strip():
            cur = g.chain(cur, [_drawtext(ctx, key="wm", text=cfg.watermark_text, font=cfg.watermark_font,
                                          size=cfg.watermark_size, color=cfg.watermark_color,
                                          opacity=cfg.watermark_opacity, box=False, motion=cfg.watermark_motion,
                                          position=cfg.watermark_position, speed=cfg.watermark_motion_speed,
                                          delay=cfg.watermark_delay, margins=margins, shadow=True)])
        return cur
    return stage


# ── Màu sắc & hiệu ứng ───────────────────────────────────────────────────

# (brightness+, contrast×, saturation×, gamma×, temperature+)
PRESET_ADJUST: dict[str, tuple[float, float, float, float, float]] = {
    "Ấm áp": (0.02, 1.05, 1.12, 1.0, 35.0),
    "Điện ảnh": (-0.03, 1.18, 0.88, 0.95, -10.0),
    "Tươi sáng": (0.06, 1.08, 1.25, 1.05, 5.0),
    "Cổ điển": (0.0, 0.95, 0.65, 1.0, 20.0),
    "Lạnh": (0.0, 1.05, 0.95, 1.0, -40.0),
}


def color_pre_stage(cfg: ColorConfig) -> Stage:
    def stage(g: Graph, cur: str, ctx: RenderContext) -> str:
        if not cfg.enable:
            return cur
        f: list[str] = []
        deg = float(cfg.rotate) % 360
        if abs(deg - 90) < 0.01:
            f.append("transpose=1")
        elif abs(deg - 270) < 0.01:
            f.append("transpose=2")
        elif abs(deg - 180) < 0.01:
            f.append("hflip,vflip")
        elif deg > 0.01:
            f.append(f"rotate={deg:.3f}*PI/180:c=black")
        if cfg.deshake:
            f.append("deshake=rx=32:ry=32:edge=mirror")
        br, ct, sat, gm, temp = cfg.brightness, cfg.contrast, cfg.saturation, cfg.gamma, cfg.temperature
        preset = PRESET_ADJUST.get(cfg.preset)
        if preset:
            br, ct, sat, gm, temp = br + preset[0], ct * preset[1], sat * preset[2], gm * preset[3], temp + preset[4]
        if any(abs(a - b) > 1e-3 for a, b in ((br, 0), (ct, 1), (sat, 1), (gm, 1))):
            f.append(f"eq=brightness={br:.3f}:contrast={ct:.3f}:saturation={sat:.3f}:gamma={gm:.3f}")
        if abs(temp) > 0.5:
            kelvin = int(6500 - max(-100.0, min(100.0, temp)) * 25)
            f.append(f"colortemperature=temperature={kelvin}")
        if cfg.sharpen > 0.01:
            f.append(f"unsharp=5:5:{min(3.0, cfg.sharpen):.2f}")
        if cfg.grain > 0.5:
            f.append(f"noise=alls={int(cfg.grain * 0.6)}:allf=t+u")
        if cfg.vignette > 0.5:
            f.append(f"vignette=angle={(0.1 + 0.9 * min(100.0, cfg.vignette) / 100) * math.pi / 2:.3f}")
        return g.chain(cur, f)
    return stage


def dedup_pre_stage(cfg: ColorConfig) -> Stage:
    """Biến thể nhẹ trước khi đổi khung hình: dịch khung, nhiễu, lệch màu, phóng & trượt."""
    def stage(g: Graph, cur: str, ctx: RenderContext) -> str:
        f: list[str] = []
        if cfg.dup_shift:
            f.append("crop=iw-4:ih-4:3:1")
        if cfg.dup_zoompan:
            # cắt 98% khung, tâm trượt chậm theo hình sin (crop hỗ trợ biến t ở x/y)
            f.append(f"crop=iw*0.98:ih*0.98:x='(iw-ow)/2+(iw-ow)/2*sin({ctx.t()}/7)':y='(ih-oh)/2+(ih-oh)/2*cos({ctx.t()}/9)'")
        if cfg.dup_color:
            f.append("eq=brightness=0.012:contrast=1.02:saturation=1.03,hue=h=1.5")
        if cfg.dup_noise:
            f.append("noise=alls=7:allf=t")
        return g.chain(cur, f)
    return stage


def dedup_audio_filters(cfg: ColorConfig) -> list[str]:
    """Lệch cao độ ~2% nhưng giữ nguyên nhịp/thời lượng (không lệch sync với hình)."""
    if not cfg.dup_audio:
        return []
    return ["asetrate=48000*1.02", "aresample=48000", "atempo=0.98039", "equalizer=f=1200:t=q:w=1:g=0.8"]


def kenburns_stage(cfg: ColorConfig) -> Stage:
    def stage(g: Graph, cur: str, ctx: RenderContext) -> str:
        if not cfg.enable or cfg.kenburns == KENBURNS[0] or ctx.duration <= 0:
            return cur
        A = max(0.01, min(0.5, cfg.kenburns_amount / 100))
        fps = ctx.fps if ctx.fps > 0 else 30.0
        # zoompan không có biến t: dùng số khung đầu vào / fps (+ mốc xem trước)
        T = f"(in/{fps:.3f}+{ctx.t0:.3f})"
        prog = f"min(1,{T}/{ctx.duration:.3f})"
        W, H = ctx.width, ctx.height
        mode = cfg.kenburns
        if mode == KENBURNS[1]:
            z, x, y = f"1+{A:.3f}*{prog}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        elif mode == KENBURNS[2]:
            z, x, y = f"1+{A:.3f}*(1-{prog})", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        elif mode == KENBURNS[3]:
            z, x, y = f"{1 + A:.3f}", f"(iw-iw/zoom)*{prog}", "ih/2-(ih/zoom/2)"
        elif mode == KENBURNS[4]:
            z, x, y = f"{1 + A:.3f}", f"(iw-iw/zoom)*(1-{prog})", "ih/2-(ih/zoom/2)"
        else:
            z, x, y = f"{1 + A:.3f}", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*{prog}"
        return g.chain(cur, [f"fps={fps:.3f}", f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps:.3f}"])
    return stage


def texture_stage(cfg: ColorConfig) -> Stage:
    """Phủ texture (video/ảnh) lên toàn khung bằng blend — film grain, bụi, light leak…"""
    def stage(g: Graph, cur: str, ctx: RenderContext) -> str:
        if not cfg.enable or not cfg.texture_enable or not cfg.texture_path or not Path(cfg.texture_path).exists():
            return cur
        path = cfg.texture_path
        is_image = Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        args = ["-loop", "1", "-i", path] if is_image else ["-stream_loop", "-1", "-i", path]
        idx = g.add_input(args)
        W, H = ctx.width, ctx.height
        tex = g.chain(f"[{idx}:v]", [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}",
                                     f"fps={ctx.fps:.3f}" if not is_image else "", "format=gbrp"], "[tex]")
        mode = cfg.texture_blend if cfg.texture_blend in ("screen", "overlay", "softlight", "multiply", "lighten",
                                                          "addition") else "screen"
        op = max(0.0, min(1.0, cfg.texture_opacity / 100))
        base = g.chain(cur, ["format=gbrp"])
        out = g.label("tx")
        g.add(f"{base}{tex}blend=all_mode={mode}:all_opacity={op:.3f}:shortest=1{out}")
        return g.chain(out, ["format=yuv420p"])
    return stage


# ── Cắt ──────────────────────────────────────────────────────────────────

def trim_spec(cfg: TrimConfig, duration: float) -> TrimSpec:
    if not cfg.enable or duration <= 0:
        return TrimSpec()
    segs: list[tuple[float, float]] = []
    for seg in cfg.segments:
        if not isinstance(seg, dict):
            continue
        s = parse_hhmmss(str(seg.get("start", "0")))
        e_raw = str(seg.get("end", "")).strip()
        e = parse_hhmmss(e_raw) if e_raw else duration
        s, e = max(0.0, s), min(duration, e)
        if e - s > 0.05:
            segs.append((s, e))
    if not segs:
        head = max(0.0, parse_hhmmss(cfg.head))
        tail = max(0.0, parse_hhmmss(cfg.tail))
        if head > 0 or tail > 0:
            e = duration - tail
            if e - head > 0.05:
                segs.append((head, e))
    segs.sort()
    return TrimSpec(segments=segs)


# ── Nhạc nền ─────────────────────────────────────────────────────────────

def bgm_extras(cfg: BGMConfig) -> Callable[[Graph, RenderContext], list[AudioExtra]]:
    def build(g: Graph, ctx: RenderContext) -> list[AudioExtra]:
        if not cfg.enable:
            return []
        out: list[AudioExtra] = []
        D = ctx.duration
        master = max(0.0, cfg.master_volume) / 100
        for clip in cfg.clips:
            if not isinstance(clip, dict):
                continue
            path = clip.get("path", "")
            if not path or not Path(path).exists():
                continue
            loop = bool(clip.get("loop", cfg.loop))
            start = max(0.0, float(clip.get("start", 0) or 0)) / ctx.speed
            offset = max(0.0, float(clip.get("offset", 0) or 0))
            vol = max(0.0, float(clip.get("volume", 100))) / 100 * master
            fi = max(0.0, float(clip.get("fade_in", cfg.fade_in)))
            fo = max(0.0, float(clip.get("fade_out", cfg.fade_out)))
            args = ["-stream_loop", "-1", "-i", path] if loop else ["-i", path]
            idx = g.add_input(args)
            end = max(0.0, float(clip.get("end", 0) or 0)) / ctx.speed
            span = max(0.1, (min(D, end) - start) if end > start else (D - start))
            # xem trước từ giữa video (ctx.t0): bỏ phần nhạc đã qua, dời phần chưa tới
            skip = max(0.0, ctx.t0 - start) if ctx.preview else 0.0
            if skip >= span:
                continue
            delay = max(0.0, start - ctx.t0) if ctx.preview else start
            left = span - skip
            f: list[str] = []
            if offset + skip > 0:
                f += [f"atrim=start={offset + skip:.3f}", "asetpts=PTS-STARTPTS"]
            f += [f"atrim=end={left:.3f}", "asetpts=PTS-STARTPTS"]
            if fi > skip:
                f.append(f"afade=t=in:st=0:d={fi - skip:.2f}")
            if fo > 0 and span > fo:
                st_out = span - fo - skip
                f.append(f"afade=t=out:st={st_out:.3f}:d={fo:.2f}" if st_out >= 0 else
                         f"afade=t=out:st=0:d={left:.2f}")
            if delay > 0:
                ms = int(delay * 1000)
                f.append(f"adelay={ms}|{ms}")
            f.append(f"volume={vol:.3f}")
            out.append(AudioExtra(label=f"[{idx}:a]", filters=f, duck=cfg.ducking))
        return out
    return build


# ── Gom tất cả cho một video ─────────────────────────────────────────────

class ModuleStages:
    """Kết quả gom stage cho pipeline/preview."""

    def __init__(self, info: MediaInfo, pre_stages: list[Stage], stages: list[Stage], trim: TrimSpec,
                 audio_extras: Callable[[Graph, RenderContext], list[AudioExtra]],
                 src_stages: list[Stage] | None = None) -> None:
        self.info = info
        self.pre_stages = pre_stages
        self.stages = stages
        self.src_stages = src_stages or []
        self.trim = trim
        self.audio_extras = audio_extras
        self.audio_orig_filters: list[str] = []


def collect(state: AppState, item: VideoItem, info: MediaInfo, *, include_blur: bool = True,
            trim_override: TrimSpec | None = None) -> ModuleStages:
    eff = info
    deg = float(state.color.rotate) % 360 if state.color.enable else 0.0
    if abs(deg - 90) < 0.01 or abs(deg - 270) < 0.01:
        eff = replace(info, width=info.height, height=info.width)
    stages = [kenburns_stage(state.color), texture_stage(state.color)]
    src_stages: list[Stage] = []
    if include_blur:
        src_stages.append(blur_stage(state.blur))
    stages.append(brand_stage(state.brand))
    ms = ModuleStages(
        info=eff,
        pre_stages=[color_pre_stage(state.color), dedup_pre_stage(state.color)],
        stages=stages, src_stages=src_stages,
        trim=trim_override if trim_override is not None else trim_spec(state.trim, info.duration),
        audio_extras=bgm_extras(state.bgm),
    )
    ms.audio_orig_filters = dedup_audio_filters(state.color)
    return ms
