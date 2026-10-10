"""Điều phối Story: giọng từng cảnh, ảnh/video AI, ghép, metadata — chạy trong thread, có tiến độ/huỷ."""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from app.core.state import TTS_PROVIDERS, AppState, TTSConfig, provider_or_default
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.gemini_client import GeminiClient
from app.services.story import ai as story_ai
from app.services.story.assembler import assemble
from app.services.story.project import Scene, StoryProject
from app.services.story.prompts import master_prompt_text
from app.services.tts.generator import make_engine, prefill_batches, qc_options, synthesize_cached, voice_id_for

logger = logging.getLogger(__name__)

ProgressCb = Callable[[float], None]
LogCb = Callable[[str], None]

RATIO_SIZES = {"16:9 (YouTube ngang)": ("16:9", 1920, 1080), "9:16 (Dọc)": ("9:16", 1080, 1920),
               "1:1 (Vuông)": ("1:1", 1080, 1080)}
RES_SCALE = {"720p": 720 / 1080, "1080p": 1.0, "4K": 2.0}


def _check(cancel: threading.Event | None, what: str) -> None:
    if cancel is not None and cancel.is_set():
        raise RenderCancelled(f"Đã huỷ {what}")


class StoryEngine:
    def __init__(self, state: AppState, ffmpeg: FFmpeg) -> None:
        self.state = state
        self.ffmpeg = ffmpeg

    # ── Tiện ích ─────────────────────────────────────────────────────────
    def client(self) -> GeminiClient:
        return GeminiClient(self.state.settings.gemini_keys())

    def output_size(self) -> tuple[str, int, int]:
        cfg = self.state.story
        ar, w, h = RATIO_SIZES.get(cfg.ratio, RATIO_SIZES["16:9 (YouTube ngang)"])
        k = RES_SCALE.get(cfg.resolution, 1.0)
        w, h = int(w * k) // 2 * 2, int(h * k) // 2 * 2
        return ar, w, h

    def _tts_config_for(self, proj: StoryProject, speaker: str) -> TTSConfig:
        """TTSConfig cho một người nói: giọng riêng của nhân vật nếu có, không thì giọng kể mặc định."""
        base = replace(self.state.tts)
        cfg = self.state.story
        base.tts_provider, base.tts_speed = provider_or_default(cfg.provider), cfg.speed
        base.set_voice(cfg.voice_id)
        ch = proj.character(speaker)
        prov = (ch.voice_provider or cfg.provider) if ch else ""
        if ch and ch.voice_id and prov in TTS_PROVIDERS:     # nhân vật còn ghi engine đã gỡ (ElevenLabs…) → giọng kể
            base.tts_provider = prov
            base.set_voice(ch.voice_id)
            base.tts_speed = ch.speed or cfg.speed
        return base

    # ── Kịch bản ─────────────────────────────────────────────────────────
    def write_script(self, proj: StoryProject, *, on_progress: ProgressCb | None = None, log: LogCb | None = None,
                     cancel: threading.Event | None = None) -> StoryProject:
        story_ai.write_script(self.client(), self.state.story.text_model, proj, on_progress=on_progress, log=log,
                              cancel=cancel)
        # giọng đọc cũ không còn khớp
        for sc in proj.scenes:
            sc.audio_path = ""
        proj.save()
        return proj

    def rewrite_scene(self, proj: StoryProject, scene: Scene, instruction: str) -> Scene:
        story_ai.regenerate_scene_text(self.client(), self.state.story.text_model, proj, scene, instruction)
        scene.audio_path = ""
        proj.save()
        return scene

    # ── Giọng ────────────────────────────────────────────────────────────
    def generate_voices(self, proj: StoryProject, *, only_missing: bool = True, scenes: list[Scene] | None = None,
                        on_progress: ProgressCb | None = None, log: LogCb | None = None,
                        cancel: threading.Event | None = None) -> int:
        targets = [s for s in (scenes or proj.scenes) if s.narration.strip()
                   and not (only_missing and s.audio_path and Path(s.audio_path).exists())]
        if not targets:
            if log:
                log("Mọi cảnh đã có giọng.")
            return 0
        audio_dir = proj.subdir("audio")
        engines: dict[str, object] = {}
        settings = self.state.settings
        qc = qc_options(settings)

        def engine_for(sc: Scene) -> tuple[TTSConfig, object, str]:
            cfg = self._tts_config_for(proj, sc.speaker)
            key = f"{cfg.tts_provider}|{cfg.edge_voice}"
            eng = engines.get(key)
            if eng is None:
                eng = engines[key] = make_engine(cfg, settings)
            return cfg, eng, voice_id_for(cfg)

        def work(sc: Scene) -> tuple[Scene, Path]:
            _check(cancel, "tạo giọng")
            cfg, eng, vid = engine_for(sc)
            cached = synthesize_cached(eng, vid, sc.narration.strip(), speed=cfg.tts_speed, pitch=cfg.tts_pitch,
                                       qc=qc)
            idx = proj.scenes.index(sc) if sc in proj.scenes else 0
            dst = audio_dir / f"scene_{idx:03d}{Path(cached).suffix or '.mp3'}"
            shutil.copyfile(cached, dst)
            return sc, dst

        done = 0
        if log:
            log(f"Tạo giọng cho {len(targets)} cảnh…")
        # engine đọc gộp được (VieNeu): lời kể các cảnh liền nhau đọc một hơi rồi tách lại
        jobs = []
        for sc in targets:
            cfg, eng, vid = engine_for(sc)
            jobs.append((eng, vid, cfg.tts_speed, cfg.tts_pitch, sc.narration.strip()))
        prefill_batches(jobs, settings, qc, log=log, check=lambda: _check(cancel, "tạo giọng"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(work, s) for s in targets]
            for fut in as_completed(futs):
                sc, dst = fut.result()
                sc.audio_path = str(dst)
                sc.duration = self.ffmpeg.duration_of(dst)
                done += 1
                if on_progress:
                    on_progress(done / len(targets))
        proj.save()
        if log:
            log(f"Xong {done} giọng · tổng ~{proj.total_duration / 60:.1f} phút")
        return done

    # ── Ảnh ──────────────────────────────────────────────────────────────
    def generate_images(self, proj: StoryProject, *, only_missing: bool = True, scenes: list[Scene] | None = None,
                        on_progress: ProgressCb | None = None, log: LogCb | None = None,
                        cancel: threading.Event | None = None) -> int:
        cfg = self.state.story
        targets = [s for s in (scenes or proj.scenes)
                   if not (only_missing and s.image_path and Path(s.image_path).exists())]
        if not targets:
            if log:
                log("Mọi cảnh đã có ảnh.")
            return 0
        client = self.client()
        ar, _, _ = self.output_size()
        style = master_prompt_text(proj.master_prompt_preset, proj.master_prompt_custom)
        img_dir = proj.subdir("images")
        # ảnh tham chiếu để giữ nhân vật đồng nhất (chỉ model Gemini image nhận ảnh vào)
        ref: Path | None = None
        if not cfg.image_model.startswith("imagen"):
            for s in proj.scenes:
                if s.image_path and Path(s.image_path).exists():
                    ref = Path(s.image_path)
                    break

        def prompt_for(sc: Scene) -> str:
            p = sc.image_prompt.strip() or sc.narration.strip()[:300]
            if style and style.split(",")[0].lower() not in p.lower():
                p = f"{p}. Style: {style}"
            if ref is not None:
                p += ". Keep the same characters' appearance as in the reference image."
            return p

        def work(sc: Scene) -> tuple[Scene, Path]:
            _check(cancel, "tạo ảnh")
            idx = proj.scenes.index(sc) if sc in proj.scenes else 0
            dst = img_dir / f"scene_{idx:03d}.png"
            client.generate_image(cfg.image_model, prompt_for(sc), dst, aspect_ratio=ar, reference_image=ref)
            return sc, dst

        done = 0
        if log:
            log(f"Tạo {len(targets)} ảnh bằng {cfg.image_model}…")
        # cảnh đầu tiên chạy trước để làm tham chiếu
        first = targets[0]
        if ref is None:
            try:
                sc, dst = work(first)
                sc.image_path = str(dst)
                ref = dst
            except RenderCancelled:
                raise
            except Exception as exc:
                logger.warning("Ảnh lỗi: %s", exc)
                if log:
                    log(f"⚠ Ảnh lỗi: {str(exc)[:220]}")
                if "429" in str(exc) or "hạn mức" in str(exc):
                    if log:
                        log("→ Bỏ qua tạo ảnh: các cảnh chưa có ảnh sẽ dùng ảnh tạm (chữ trên nền màu) khi ghép video.")
                    return 0
            done += 1
            targets = targets[1:]
            proj.save()
            if on_progress:
                on_progress(done / (done + len(targets)))
        total = done + len(targets)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(work, s) for s in targets]
            for fut in as_completed(futs):
                try:
                    sc, dst = fut.result()
                    sc.image_path = str(dst)
                except RenderCancelled:
                    raise
                except Exception as exc:
                    logger.warning("Ảnh lỗi: %s", exc)
                    if log:
                        log(f"⚠ Ảnh lỗi: {str(exc)[:160]}")
                done += 1
                if on_progress:
                    on_progress(done / max(1, total))
                proj.save()
        if log:
            log(f"Xong ảnh: {sum(1 for s in proj.scenes if s.image_path)} / {len(proj.scenes)} cảnh có ảnh")
        return done

    # ── Video AI (Veo) ───────────────────────────────────────────────────
    def generate_videos(self, proj: StoryProject, *, scenes: list[Scene] | None = None,
                        on_progress: ProgressCb | None = None, log: LogCb | None = None,
                        cancel: threading.Event | None = None) -> int:
        cfg = self.state.story
        targets = [s for s in (scenes or proj.scenes) if not (s.video_path and Path(s.video_path).exists())]
        if not targets:
            return 0
        client = self.client()
        ar, _, _ = self.output_size()
        vdir = proj.subdir("video")
        done = 0
        for sc in targets:
            _check(cancel, "tạo video")
            idx = proj.scenes.index(sc) if sc in proj.scenes else 0
            dst = vdir / f"scene_{idx:03d}.mp4"
            img = Path(sc.image_path) if sc.image_path and Path(sc.image_path).exists() else None
            if log:
                log(f"Veo: cảnh {idx + 1} ({'từ ảnh' if img else 'từ prompt'})…")
            prompt = sc.image_prompt.strip() or sc.narration[:300]
            prompt += ". Slow cinematic camera movement, natural motion, no text."
            try:
                client.generate_video(cfg.video_model, prompt, dst, aspect_ratio=ar, duration=cfg.video_duration,
                                      image=img, cancel=cancel)
                sc.video_path = str(dst)
            except RenderCancelled:
                raise
            except Exception as exc:
                logger.warning("Veo lỗi: %s", exc)
                if log:
                    log(f"⚠ Veo cảnh {idx + 1}: {str(exc)[:200]}")
            done += 1
            proj.save()
            if on_progress:
                on_progress(done / len(targets))
        return done

    # ── Ghép ─────────────────────────────────────────────────────────────
    def assemble(self, proj: StoryProject, *, on_progress: ProgressCb | None = None, log: LogCb | None = None,
                 cancel: threading.Event | None = None) -> tuple[Path, Path]:
        cfg = self.state.story
        _, w, h = self.output_size()
        return assemble(proj, self.ffmpeg, width=w, height=h, fps=30, kenburns_amount=cfg.kenburns_amount / 100,
                        gap=cfg.gap, on_progress=on_progress, log=log, cancel=cancel)

    # ── Metadata ─────────────────────────────────────────────────────────
    def metadata(self, proj: StoryProject, *, log: LogCb | None = None) -> dict:
        timeline_lines = []
        t = 0.0
        for i, s in enumerate(proj.scenes):
            if i % max(1, len(proj.scenes) // 12) == 0:
                m, sec = divmod(int(t), 60)
                timeline_lines.append(f"{m:02d}:{sec:02d} — {s.narration[:80]}")
            t += (s.duration or 8.0) + self.state.story.gap
        data = story_ai.generate_metadata(self.client(), self.state.story.text_model, proj,
                                          timeline="\n".join(timeline_lines))
        proj.save()
        if log:
            log("Đã tạo metadata YouTube.")
        return data

    def thumbnail(self, proj: StoryProject, *, log: LogCb | None = None) -> Path:
        prompt = proj.thumbnail_prompt or f"YouTube thumbnail for: {proj.title or proj.name}. Dramatic, high contrast."
        dst = proj.subdir("images") / "thumbnail.png"
        self.client().generate_image(self.state.story.image_model, prompt, dst, aspect_ratio="16:9")
        if log:
            log(f"Thumbnail: {dst}")
        return dst
