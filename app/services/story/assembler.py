"""Ghép các cảnh (ảnh/clip + giọng) thành một video thô + SRT, sẵn sàng đưa vào pipeline render chính."""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config.paths import ASSETS_DIR
from app.core.state import VideoConfig
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.render_pipeline import cpu_fallback, pick_encoder
from app.services.srt_utils import Cue, save_srt, wrap_lines
from app.services.story.project import Scene, StoryProject
from app.services.system_fonts import font_file

logger = logging.getLogger(__name__)

SFX_DIR = ASSETS_DIR / "sfx"
_SENT_RE = re.compile(r"(?<=[.!?…。！？])\s+")


def placeholder_image(path: Path, width: int, height: int, index: int, text: str) -> Path:
    """Ảnh tạm khi cảnh chưa có hình: nền tối + số cảnh + vài chữ đầu."""
    img = Image.new("RGB", (width, height), (24, 26, 32))
    d = ImageDraw.Draw(img)
    for y in range(height):
        shade = int(24 + 30 * y / height)
        d.line([(0, y), (width, y)], fill=(shade, shade + 2, shade + 8))
    ff = font_file("Segoe UI", bold=True) or font_file("Arial", bold=True)
    try:
        big = ImageFont.truetype(str(ff), int(height * 0.12)) if ff else ImageFont.load_default()
        small = ImageFont.truetype(str(ff), int(height * 0.035)) if ff else ImageFont.load_default()
    except Exception:
        big = small = ImageFont.load_default()
    d.text((width * 0.06, height * 0.36), f"Cảnh {index + 1}", font=big, fill=(6, 182, 212))
    d.text((width * 0.06, height * 0.55), wrap_lines(text[:120], 60), font=small, fill=(200, 205, 215))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_RE.split(text.strip()) if p.strip()]
    out: list[str] = []
    for p in parts:
        # câu quá dài → chia đôi ở dấu phẩy
        while len(p) > 110:
            cut = p.rfind(",", 30, 100)
            if cut < 0:
                cut = p.rfind(" ", 30, 100)
            if cut < 0:
                break
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    return out or [text.strip()]


def scene_cues(scenes: list[Scene], starts: list[float], gap: float) -> list[Cue]:
    """Phụ đề theo câu, chia thời gian tỉ lệ số ký tự trong thời lượng audio của cảnh."""
    cues: list[Cue] = []
    for sc, start in zip(scenes, starts):
        sents = split_sentences(sc.narration)
        total = sum(len(s) for s in sents) or 1
        audio_dur = max(0.3, sc.duration)
        t = start
        for s in sents:
            d = audio_dur * len(s) / total
            cues.append(Cue(len(cues) + 1, t, max(t + 0.3, t + d - 0.05), wrap_lines(s)))
            t += d
    return cues


def assemble(proj: StoryProject, ffmpeg: FFmpeg, *, width: int, height: int, fps: int = 30,
             kenburns_amount: float = 0.08, gap: float = 0.4, vcfg: VideoConfig | None = None,
             on_progress: Callable[[float], None] | None = None,
             log: Callable[[str], None] | None = None,
             cancel: threading.Event | None = None) -> tuple[Path, Path]:
    scenes = [s for s in proj.scenes if s.narration.strip()]
    if not scenes:
        raise RuntimeError("Dự án chưa có cảnh nào.")
    missing = [i + 1 for i, s in enumerate(scenes) if not (s.audio_path and Path(s.audio_path).exists())]
    if missing:
        raise RuntimeError(f"Các cảnh chưa có giọng đọc: {missing[:10]}{'…' if len(missing) > 10 else ''}. "
                           "Hãy bấm “Tạo giọng” trước.")
    work = proj.subdir("build")
    for old in work.glob("scene_*.mp4"):
        try:
            old.unlink()
        except OSError:
            pass
    enc_cfg = vcfg or VideoConfig(quality=18, encode_preset="Nhanh")
    enc = pick_encoder(ffmpeg, enc_cfg, log)
    done: list[tuple[list[str], list[str], float]] = []   # cảnh đã ghép: GPU lỗi giữa chừng thì ghép lại bằng CPU
    starts: list[float] = []
    t_cursor = 0.0
    total = len(scenes)

    def _check() -> None:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ ghép video")

    for i, sc in enumerate(scenes):
        _check()
        sc.duration = ffmpeg.duration_of(sc.audio_path)
        dur = sc.duration + gap
        starts.append(t_cursor)
        t_cursor += dur
        visual = sc.visual if sc.visual and Path(sc.visual).exists() else ""
        is_video = bool(visual) and Path(visual).suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".m4v")
        if not visual:
            visual = str(placeholder_image(work / f"placeholder_{i:03d}.png", width, height, i, sc.narration))
        out_clip = work / f"scene_{i:03d}.mp4"
        frames = max(2, int(round(dur * fps)))
        A = max(0.0, min(0.5, kenburns_amount))
        if is_video:
            args = ["-stream_loop", "-1", "-i", visual, "-i", sc.audio_path, "-filter_complex",
                    f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                    f"fps={fps},format=yuv420p[v];[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                    f"apad=pad_dur={gap:.3f}[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}"]
        else:
            # luân phiên zoom vào / zoom ra / lia ngang để đỡ đơn điệu
            mode = i % 3
            if A <= 0.001:
                zp = f"zoompan=z=1:d={frames}:s={width}x{height}:fps={fps}"
            elif mode == 0:
                zp = (f"zoompan=z='1+{A:.3f}*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                      f"d={frames}:s={width}x{height}:fps={fps}")
            elif mode == 1:
                zp = (f"zoompan=z='1+{A:.3f}*(1-on/{frames})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                      f"d={frames}:s={width}x{height}:fps={fps}")
            else:
                zp = (f"zoompan=z='{1 + A:.3f}':x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)':"
                      f"d={frames}:s={width}x{height}:fps={fps}")
            # scale ảnh lớn hơn khung một chút để zoompan không bị vỡ hạt
            args = ["-loop", "1", "-framerate", str(fps), "-i", visual, "-i", sc.audio_path, "-filter_complex",
                    f"[0:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                    f"crop={width * 2}:{height * 2},{zp},format=yuv420p[v];"
                    f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad=pad_dur={gap:.3f}[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}"]
        tail = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out_clip)]
        if log:
            log(f"Ghép cảnh {i + 1}/{total} ({dur:.1f}s)")
        try:
            ffmpeg.run([*args, *enc, *tail], duration=dur, cancel=cancel, log_tag=f"scene{i}")
        except Exception as exc:
            if not cpu_fallback(ffmpeg, enc, exc, log):
                raise
            enc = pick_encoder(ffmpeg, dataclasses.replace(enc_cfg, use_gpu=False))
            # nối bằng -c copy cần mọi cảnh cùng một bộ mã hoá → cảnh đã ghép bằng GPU cũng ghép lại
            for j, (a, t, d) in enumerate(done):
                _check()
                ffmpeg.run([*a, *enc, *t], duration=d, cancel=cancel, log_tag=f"scene{j}")
            ffmpeg.run([*args, *enc, *tail], duration=dur, cancel=cancel, log_tag=f"scene{i}")
        done.append((args, tail, dur))
        if on_progress:
            on_progress(0.9 * (i + 1) / total)

    # ── Nối ─────────────────────────────────────────────────────────────
    list_file = work / "list.txt"
    list_file.write_text("".join(f"file 'scene_{i:03d}.mp4'\n" for i in range(total)), encoding="utf-8")
    raw = work / "story_raw.mp4"
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "-movflags", "+faststart",
                str(raw)], cwd=work, cancel=cancel, log_tag="concat")

    # ── SFX ─────────────────────────────────────────────────────────────
    final = proj.dir / f"{proj.dir.name}_story.mp4"
    sfx_inputs: list[tuple[Path, float]] = []
    for sc, start in zip(scenes, starts):
        if sc.sfx:
            cand = Path(sc.sfx) if Path(sc.sfx).suffix else None
            if cand is None or not cand.exists():
                for ext in (".wav", ".mp3", ".ogg"):
                    c = SFX_DIR / f"{sc.sfx}{ext}"
                    if c.exists():
                        cand = c
                        break
            if cand and cand.exists():
                sfx_inputs.append((cand, start))
    if sfx_inputs:
        if log:
            log(f"Trộn {len(sfx_inputs)} hiệu ứng âm thanh…")
        args = ["-i", str(raw)]
        parts = []
        for j, (p, start) in enumerate(sfx_inputs):
            args += ["-i", str(p)]
            ms = int(start * 1000)
            parts.append(f"[{j + 1}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms},volume=0.7[s{j}]")
        labels = "".join(f"[s{j}]" for j in range(len(sfx_inputs)))
        parts.append(f"[0:a]{labels}amix=inputs={len(sfx_inputs) + 1}:duration=first:dropout_transition=0:normalize=0[a]")
        args += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                 "-b:a", "192k", "-movflags", "+faststart", str(final)]
        ffmpeg.run(args, duration=t_cursor, cancel=cancel, log_tag="sfx")
    else:
        if final.exists():
            final.unlink()
        raw.replace(final)

    srt = proj.dir / f"{proj.dir.name}_story.srt"
    save_srt(scene_cues(scenes, starts, gap), srt)
    proj.assembled_path, proj.srt_path = str(final), str(srt)
    proj.save()
    if on_progress:
        on_progress(1.0)
    if log:
        log(f"Ghép xong: {final.name} ({t_cursor / 60:.1f} phút) + {srt.name}")
    return final, srt
