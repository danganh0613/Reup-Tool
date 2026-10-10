"""Phát xem trước giống bản render: cùng chuỗi filter, cùng phụ đề ASS, cùng cách trộn âm thanh.

``LiveSpec`` chụp cấu hình lúc bấm Phát và dựng lệnh FFmpeg cho từng đoạn phát:

- Hình: toàn bộ filter của bản render (khung ra, phóng, lật, màu, hiệu ứng, chống trùng, che mờ, logo/chữ/viền,
  Ken Burns, phụ đề ASS thật) → khung RGB thu về đúng cỡ ô xem trước, giới hạn 30 khung/giây.
- Tiếng: âm gốc (âm lượng, lọc giọng, chống trùng) + giọng lồng (TTS) + nhạc nền (fade, ducking) + chuẩn hoá —
  chính ``build_audio_graph`` của bản render, cắt đúng đoạn đang phát.
- Cắt (Trim) + tốc độ: chỉ phát các đoạn giữ lại, nhịp phát theo tốc độ video.

Chạy trong thread của Player (không gọi Tk).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from app.config.paths import PREVIEW_CACHE_DIR
from app.core.state import AppState, VideoItem
from app.services import render_pipeline as rp
from app.services import stages as stages_mod
from app.services.ffmpeg import FFmpeg, MediaInfo
from app.services.srt_utils import Cue
from app.services.subtitle_style import write_ass
from app.services.word_timing import speech_span

logger = logging.getLogger(__name__)

MAX_FPS = 30.0


@dataclass
class VideoCmd:
    cmd: list[str]
    cwd: Path
    width: int
    height: int
    fps: float


def _even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


class LiveSpec:
    def __init__(self, state: AppState, item: VideoItem, info: MediaInfo, ffmpeg: FFmpeg, *,
                 cues: list[Cue] | None, voice: Path | None, voice_trimmed: bool,
                 effects: bool = True, show_blur: bool = True) -> None:
        self.state = state
        self.item = item
        self.info = info
        self.ffmpeg = ffmpeg
        self.vcfg = state.video
        self.scfg = stages_mod.subtitle_for(state, item, info)     # khung có thể bám chỗ chữ cứng của video này
        self.effects = effects
        self.show_blur = show_blur
        self.mods = stages_mod.collect(state, item, info, include_blur=show_blur)
        self.speed = max(0.25, min(4.0, self.vcfg.speed / 100.0))
        trim = self.mods.trim
        self.segments: list[tuple[float, float]] | None = list(trim.segments) if trim.active else None
        self.cues = cues or []
        self.voice = voice
        self.voice_trimmed = voice_trimmed      # track giọng tính theo thời gian sau cắt
        self.vspeed = rp.voice_timeline_speed(voice)     # track mới dựng theo mốc video ra (đã đổi tốc độ)
        self._duck: tuple[str, Path | None] | None = None
        self.no_vocals: Path | None = None
        if self.vcfg.demucs_enable and self.vcfg.keep_original_audio and not self.vcfg.mute and info.has_audio:
            from app.services.vocal_remove import cached_no_vocals

            self.no_vocals = cached_no_vocals(item.path)
            if self.no_vocals is None:            # chưa khử xong → nghe như bản render dự phòng: giọng gốc rất nhỏ
                self.vcfg = replace(self.vcfg, original_volume=min(self.vcfg.original_volume, 8.0))
        self.work = PREVIEW_CACHE_DIR / "live"
        self.work.mkdir(parents=True, exist_ok=True)

    # ── Thời gian ─────────────────────────────────────────────────────────
    def kept_before(self, src_t: float) -> float:
        """Thời lượng nguồn đã giữ lại trước mốc ``src_t`` (thời gian của track giọng khi có cắt)."""
        if not self.segments:
            return src_t
        total = 0.0
        for a, b in self.segments:
            if src_t <= a:
                break
            total += min(src_t, b) - a
        return total

    # ── Hình ──────────────────────────────────────────────────────────────
    def video_cmd(self, src_t: float, src_end: float, box: tuple[int, int] | None, max_width: int = 960) -> VideoCmd:
        info, vcfg = self.mods.info, self.vcfg
        speed = self.speed
        geo = rp.output_geometry(info, vcfg)
        if box and box[0] > 0 and box[1] > 0:
            s = min(box[0] / geo.width, box[1] / geo.height)
        else:
            s = min(1.0, max_width / geo.width)
        tw, th = _even(geo.width * s), _even(geo.height * s)
        fps = min(MAX_FPS, info.fps or MAX_FPS)
        ass_name = None
        if self.cues:
            # phụ đề từ mốc đang phát: dời về 0 rồi co theo tốc độ — giống hệt thời gian của bản render
            sub = []
            for c in self.cues:
                if c.end <= src_t or c.start >= src_end:
                    continue
                # «anim»: đoạn giọng thật của câu (đã đọc dở từ trước → âm) để chữ nổi đúng chữ đang đọc
                a0, d = speech_span(c)
                sub.append(Cue(len(sub) + 1, max(0.0, c.start - src_t) / speed, (c.end - src_t) / speed, c.text,
                               {**c.meta, "anim": ((a0 - src_t) / speed, d / speed)}))
            if sub:          # ASS theo cỡ bản render; libass tự co theo khung nhỏ đang phát
                write_ass(sub, self.scfg, geo.width, geo.height, self.work / "live.ass")
                ass_name = "live.ass"
        vg = rp.build_video_graph(info, vcfg, work_dir=self.work, ass_name=ass_name, stages=self.mods.stages,
                                  pre_stages=self.mods.pre_stages, src_stages=self.mods.src_stages,
                                  include_fps=False, t0=src_t / speed, preview=True, first_extra_input=1,
                                  preview_scale=min(1.0, tw / geo.width))
        graph = f"{vg.graph.text()};[vout]scale={tw}:{th},fps={fps:.3f},format=rgb24[pv]"
        cmd = [self.ffmpeg.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{max(0.0, src_t):.3f}", "-t", f"{max(0.05, src_end - src_t):.3f}", "-i", self.item.path]
        for extra in vg.graph.extra_inputs:
            cmd += extra
        cmd += ["-filter_complex", graph, "-map", "[pv]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        return VideoCmd(cmd=cmd, cwd=self.work, width=tw, height=th, fps=fps)

    # ── Tiếng ─────────────────────────────────────────────────────────────
    def audio_cmd(self, src_t: float, out_dur: float, out_wav: Path, volume: float = 1.0) -> list[str] | None:
        """Lệnh FFmpeg ghi ``out_wav``: âm thanh bản render của [src_t, src_t + out_dur×tốc độ]."""
        info, vcfg = self.info, self.vcfg
        speed = self.speed
        g = rp.Graph(1)
        geo = rp.output_geometry(self.mods.info, vcfg)
        full = (sum(b - a for a, b in self.segments) if self.segments else info.duration) / speed
        ctx = rp.RenderContext(width=geo.width, height=geo.height, fps=info.fps or 30.0, duration=full,
                               work_dir=self.work, t0=self.kept_before(src_t) / speed, preview=True, speed=speed,
                               src_width=max(2, info.width), src_height=max(2, info.height), src_t0=src_t)
        voice_label = None
        if self.voice is not None and self.state.tts.enable_tts:
            # mốc trong track: track cũ theo mốc nguồn, track mới theo mốc video ra (÷ tốc độ lúc dựng)
            vt = (self.kept_before(src_t) if self.voice_trimmed else src_t) / self.vspeed
            voice_label = f"[{g.add_input(['-ss', f'{vt:.3f}', '-i', str(self.voice)])}:a]"
        audio_src = "[0:a]" if info.has_audio else None
        if self.no_vocals is not None:           # âm gốc đã khử giọng nói (bản render cũng dùng file này)
            audio_src = f"[{g.add_input(['-ss', f'{src_t:.3f}', '-i', str(self.no_vocals)])}:a]"
        extras = self.mods.audio_extras(g, ctx) if self.mods.audio_extras else []
        duck = None
        env = self._duck_env(full) if voice_label and (
            (vcfg.adaptive_ducking and vcfg.keep_original_audio and audio_src) or any(ex.duck for ex in extras)) else None
        if env is not None:                      # cùng đường bao hạ nền như bản render
            duck = g.chain(f"[{g.add_input(['-ss', f'{ctx.t0:.3f}', '-i', str(env)])}:a]",
                           ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"], "[duckenv]")
        aout = rp.build_audio_graph(g, info, vcfg, audio_src=audio_src,
                                    voice_label=voice_label, extras=extras, duration=out_dur,
                                    orig_filters=self.mods.audio_orig_filters, voice_tempo=speed / self.vspeed,
                                    duck_label=duck, preview_loudness=self.state.settings.loudness_target)
        if not aout:
            return None
        g.add(f"{aout}volume={max(0.0, volume):.3f}[pout]")
        cmd = ["-ss", f"{max(0.0, src_t):.3f}", "-t", f"{out_dur * speed + 0.05:.3f}", "-i", self.item.path]
        for extra in g.extra_inputs:
            cmd += extra
        cmd += ["-filter_complex", g.text(), "-map", "[pout]", "-t", f"{out_dur:.3f}", "-ac", "2", "-ar", "44100",
                "-c:a", "pcm_s16le", str(out_wav)]
        return cmd

    def _duck_env(self, full: float) -> Path | None:
        """Đường bao hạ nền cho cả video (tính một lần, giữ theo track giọng + độ dài)."""
        try:
            key = f"{self.voice}|{self.voice.stat().st_mtime_ns}|{full:.2f}|{self.speed}|{self.vspeed}"
        except OSError:
            return None
        if self._duck is None or self._duck[0] != key:
            try:
                env = rp.duck_envelope(self.voice, self.work / "duck.wav", duration=full,
                                       tempo=self.speed / self.vspeed)
            except Exception:
                logger.exception("Không dựng được đường bao hạ nền cho xem trước")
                env = None
            self._duck = (key, env)
        return self._duck[1]

    def signature(self) -> str:
        """Khoá cache audio: đổi cấu hình âm thanh → trích lại."""
        v, t, b, c = self.vcfg, self.state.tts, self.state.bgm, self.state.color
        try:
            vm = int(self.voice.stat().st_mtime) if self.voice else 0
        except OSError:
            vm = 0
        return repr((v.speed, v.mute, v.keep_original_audio, v.original_volume, v.tts_volume, v.vocal_filter,
                     v.normalize_audio, self.state.settings.loudness_target, v.adaptive_ducking, t.enable_tts,
                     str(self.voice), vm, self.voice_trimmed, self.vspeed,
                     b.enable, b.clips, b.master_volume, b.ducking, b.loop, b.fade_in, b.fade_out,
                     self.mods.audio_orig_filters, c.enable, self.segments, str(self.no_vocals)))
