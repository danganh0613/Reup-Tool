"""Điều phối xử lý: probe → nhận diện → dịch → lồng tiếng → render, chạy nền.

- ``Pipeline`` chứa từng bước, dùng được riêng lẻ (nút "Nhận diện ngay"…).
- ``JobRunner`` chạy cả hàng đợi trong một thread.
- ``TaskRunner`` chạy một tác vụ lẻ trong thread riêng (mỗi lúc một tác vụ).
Mọi callback về UI đều đi qua ``ui_call`` (root.after) nên an toàn thread.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.config.paths import JOBS_DIR, SUBS_DIR, VOICE_DIR
from app.core.state import SRT_MODES, SRT_SOURCES, TRIM_MODES, TTS_SOURCES, AppState, VideoItem
from app.services import render_pipeline as rp
from app.services import stages as stages_mod
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, load_srt, merge_bilingual, save_srt, shift
from app.services.subtitle_style import write_ass

logger = logging.getLogger(__name__)

ProgressCb = Callable[[float], None]
LogCb = Callable[[str], None]


def select_sub_cues(scfg, orig: list[Cue], translated: list[Cue] | None) -> list[Cue]:
    """Chọn danh sách cue ghi lên video theo chế độ hiển thị, đã áp trễ."""
    if scfg.srt_mode == SRT_MODES[1] and translated:
        cues = translated
    elif scfg.srt_mode == SRT_MODES[2] and translated:
        cues = merge_bilingual(translated, orig)
    else:
        cues = orig
    return shift(cues, scfg.sub_delay)


def _safe_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip() or "video"


class Pipeline:
    def __init__(self, state: AppState, ffmpeg: FFmpeg) -> None:
        self.state = state
        self.ffmpeg = ffmpeg
        self._srt_cache: dict[tuple[str, float], list[Cue]] = {}
        self.audio_only = False

    # ── Bước lẻ ───────────────────────────────────────────────────────────
    def probe(self, item: VideoItem) -> None:
        info = self.ffmpeg.probe(item.path)
        item.duration, item.width, item.height, item.fps, item.has_audio = (
            info.duration, info.width, info.height, info.fps, info.has_audio)

    def subtitle_cues(self, item: VideoItem) -> list[Cue]:
        """Cue sẽ hiển thị cho ``item`` theo cấu hình hiện tại (có cache theo mtime)."""
        def _load(path: str | None) -> list[Cue] | None:
            if not path or not Path(path).exists():
                return None
            key = (path, Path(path).stat().st_mtime)
            if key not in self._srt_cache:
                self._srt_cache.clear()
                self._srt_cache[key] = load_srt(path)
            return self._srt_cache[key]

        orig = _load(item.srt_path) or []
        tr = _load(item.srt_translated_path)
        return select_sub_cues(self.state.subtitle, orig, tr)

    def job_dir(self, item: VideoItem) -> Path:
        h = hashlib.sha1(item.path.encode("utf-8")).hexdigest()[:8]
        return JOBS_DIR / f"{_safe_stem(item.stem)[:40]}_{h}"

    def transcribe(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                   cancel: threading.Event | None = None, source_path: str | None = None) -> list[Cue]:
        cfg = self.state.subtitle
        src = source_path or item.path
        if cfg.stt_engine.startswith("Gemini"):
            from app.services.stt_gemini import transcribe as gemini_transcribe

            res = gemini_transcribe(src, self.ffmpeg, api_key=self.state.settings.gemini_keys(), model=self.state.settings.translate_model
                                    if self.state.settings.translate_provider == "Gemini" else "gemini-flash-latest",
                                    language=cfg.stt_language, work_dir=self.job_dir(item), on_progress=progress,
                                    log=log, cancel=cancel)
        else:
            from app.services.whisper_stt import transcribe

            model = cfg.stt_model
            compute = self.state.settings.whisper_compute
            if cfg.weak_pc:
                model = "tiny" if model in ("large-v3", "large-v3-turbo", "medium") else model
                compute = "int8"
            res = transcribe(src, self.ffmpeg, model_size=model, language=cfg.stt_language,
                             device=cfg.stt_device, compute=compute,
                             work_dir=self.job_dir(item), on_progress=progress, log=log, cancel=cancel)
        if not res.cues:
            raise RuntimeError("Không nhận ra lời nói nào trong file — file có tiếng nói không? Thử model lớn hơn "
                               "(small / large-v3-turbo) hoặc chọn đúng ngôn ngữ thay vì auto.")
        out = SUBS_DIR / f"{_safe_stem(Path(src).stem)}.srt"
        save_srt(res.cues, out)
        item.srt_path = str(out)
        item.srt_translated_path = None
        if log:
            log(f"Nhận diện xong: {len(res.cues)} cue · ngôn ngữ {res.language} "
                f"({res.language_probability:.0%}) → {out.name}")
        return res.cues

    def translate(self, item: VideoItem, cues: list[Cue] | None = None, *, progress: ProgressCb | None = None,
                  log: LogCb | None = None, cancel: threading.Event | None = None) -> list[Cue]:
        from app.services.translate_router import translate_cues

        cfg = self.state.subtitle
        if cues is None:
            if not item.srt_path:
                raise RuntimeError("Chưa có phụ đề gốc để dịch.")
            cues = load_srt(item.srt_path)
        out_cues = translate_cues(cues, settings=self.state.settings, target=cfg.translate_target,
                                  source_hint=cfg.translate_source, style=cfg.translate_style, reflow=cfg.ai_reflow,
                                  srt_path=item.srt_path, on_progress=progress, log=log, cancel=cancel)
        from app.services.translate_router import LANG_CODE

        tag = LANG_CODE.get(cfg.translate_target) or _safe_stem(cfg.translate_target.split(" ")[-1]).lower()[:8] or "dich"
        out = SUBS_DIR / f"{_safe_stem(item.stem)}.{tag}.srt"
        save_srt(out_cues, out)
        item.srt_translated_path = str(out)
        if log:
            log(f"Dịch xong → {out.name}")
        return out_cues

    def voice_signature(self, item: VideoItem, srt_path: str, extra_key: str = "") -> str:
        t = self.state.tts
        try:
            mtime = int(Path(srt_path).stat().st_mtime)
        except OSError:
            mtime = 0
        raw = "|".join(str(x) for x in (
            t.tts_provider, t.edge_voice, t.eleven_voice_id, t.eleven_model, t.eleven_stability, t.eleven_similarity,
            t.eleven_style, t.eleven_speaker_boost, t.google_voice, t.vieneu_voice, t.tts_speed, t.tts_pitch, t.max_tempo,
            t.min_gap, t.soft_timing, t.natural_sync, srt_path, mtime, extra_key))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    def generate_voice(self, item: VideoItem, cues: list[Cue] | None = None, *, srt_path: str | None = None,
                       progress: ProgressCb | None = None, log: LogCb | None = None,
                       cancel: threading.Event | None = None, force: bool = False, extra_key: str = "",
                       fit_duration: float | None = None) -> Path:
        from app.services.tts.generator import generate_voice_track, voice_id_for

        if cues is None:
            srt_path = srt_path or self._tts_srt_path(item)
            cues = load_srt(srt_path)
        srt_path = srt_path or item.srt_path or ""
        out = VOICE_DIR / f"{_safe_stem(item.stem)}_{self.voice_signature(item, srt_path, extra_key)}.wav"
        if out.exists() and not force:
            if log:
                log(f"Dùng lại track giọng đã có: {out.name}")
            item.voice_path = str(out)
            return out
        res = generate_voice_track(cues, self.state.tts, self.state.settings, self.ffmpeg, out,
                                   on_progress=progress, log=log, cancel=cancel, fit_duration=fit_duration)
        synced = out.with_suffix(".srt")
        if res.synced_cues:
            save_srt(res.synced_cues, synced)      # phụ đề đã dời theo vị trí thật của giọng
        elif synced.exists():
            synced.unlink()
        # ghi chú nguồn để "Thư viện voice" biết track này tạo từ SRT nào
        try:
            import json as _json
            out.with_suffix(".json").write_text(_json.dumps({
                "video": item.path, "srt": srt_path, "provider": self.state.tts.tts_provider,
                "voice": voice_id_for(self.state.tts), "speed": self.state.tts.tts_speed,
                "duration": res.duration, "cues": res.cue_count, "created": time.time(),
                "synced_srt": str(synced) if res.synced_cues else ""}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        item.voice_path = str(out)
        return out

    def _tts_srt_path(self, item: VideoItem) -> str:
        want_translated = self.state.tts.tts_source == TTS_SOURCES[0]
        if want_translated and item.srt_translated_path:
            return item.srt_translated_path
        if item.srt_path:
            return item.srt_path
        raise RuntimeError("Chưa có phụ đề để tạo giọng (nhận diện hoặc chọn file SRT trước).")

    # ── Chuỗi đầy đủ cho một video ────────────────────────────────────────
    def process(self, item: VideoItem, *, on_update: Callable[[VideoItem], None],
                log: LogCb, cancel: threading.Event) -> Path:
        st = self.state
        item.status, item.progress, item.message, item.output_path = "Đang xử lý", 0.0, "Đọc thông tin", None
        on_update(item)
        self.probe(item)
        info = self.ffmpeg.probe(item.path)
        if info.width <= 0 or info.height <= 0:
            raise RuntimeError("File không có luồng hình (audio thuần) — chỉ xử lý được video.")
        mods = stages_mod.collect(st, item, info)
        trim = mods.trim
        # "Xuất mỗi đoạn thành 1 file": render lần lượt từng đoạn thành file riêng
        if trim.active and st.trim.mode == TRIM_MODES[1] and len(trim.segments) > 1:
            outs: list[Path] = []
            for si, seg in enumerate(trim.segments):
                sub_trim = rp.TrimSpec(segments=[seg])
                log(f"Đoạn {si + 1}/{len(trim.segments)}: {seg[0]:.1f}s → {seg[1]:.1f}s")
                outs.append(self._render_one(item, info, sub_trim, on_update=on_update, log=log, cancel=cancel,
                                             suffix=f"_p{si + 1}", final=False))
            item.output_path = str(outs[-1])
            item.status, item.progress, item.message = "Xong", 1.0, f"{len(outs)} file"
            on_update(item)
            return outs[-1]
        return self._render_one(item, info, trim, on_update=on_update, log=log, cancel=cancel)

    def _render_one(self, item: VideoItem, info, trim, *, on_update, log, cancel, suffix: str = "",
                    final: bool = True) -> Path:
        """``final=False``: một đoạn trong chế độ xuất nhiều file — không đánh dấu "Xong" để bộ đếm tiến độ
        của hàng đợi không nhảy trước."""
        st = self.state
        vcfg, scfg, tcfg = st.video, st.subtitle, st.tts
        mods = stages_mod.collect(st, item, info, trim_override=trim)

        def stage(name: str, base: float, span: float) -> ProgressCb:
            last = [0.0]

            def cb(p: float) -> None:
                now = time.time()
                if now - last[0] < 0.15 and p < 1.0:
                    return
                last[0] = now
                item.progress = base + span * max(0.0, min(1.0, p))
                item.message = name
                on_update(item)
            return cb

        trim_key = repr([(round(a, 3), round(b, 3)) for a, b in trim.segments]) if trim.active else ""
        if trim.active:
            log(f"Cắt: giữ {len(trim.segments)} đoạn · {trim.kept_duration:.1f}s")
        elif st.trim.enable:
            log("⚠ Cắt đang bật nhưng mốc không hợp lệ (vượt thời lượng, đầu ≥ cuối…) — giữ nguyên video.")

        sub_on = scfg.enable_subtitle
        tts_on = tcfg.enable_tts
        need_cues = sub_on or tts_on
        want_translated = (sub_on and scfg.srt_mode in (SRT_MODES[1], SRT_MODES[2])) or \
                          (tts_on and tcfg.tts_source == TTS_SOURCES[0])

        cues_orig: list[Cue] = []
        cues_tr: list[Cue] | None = None
        if need_cues:
            if item.srt_path and Path(item.srt_path).exists():
                cues_orig = load_srt(item.srt_path)
            elif scfg.srt_source == SRT_SOURCES[0]:
                cues_orig = self.transcribe(item, progress=stage("Nhận diện giọng nói", 0.0, 0.3), log=log,
                                            cancel=cancel)
                on_update(item)
            else:
                guess = Path(item.path).with_suffix(".srt")
                if guess.exists():
                    item.srt_path = str(guess)
                    cues_orig = load_srt(guess)
                    log(f"Dùng SRT cạnh video: {guess.name}")
                else:
                    raise RuntimeError("Chưa chọn file SRT cho video này.")
            if want_translated:
                if item.srt_translated_path and Path(item.srt_translated_path).exists():
                    cues_tr = load_srt(item.srt_translated_path)
                elif scfg.translate_enable:
                    cues_tr = self.translate(item, cues_orig, progress=stage("Dịch phụ đề", 0.3, 0.15), log=log,
                                             cancel=cancel)
                    on_update(item)
                else:
                    log("⚠ Cần bản dịch nhưng chưa bật Dịch — dùng phụ đề gốc.")
                    cues_tr = None

        voice_path: Path | None = None
        if tts_on:
            src_cues = cues_tr if (tcfg.tts_source == TTS_SOURCES[0] and cues_tr) else cues_orig
            src_path = item.srt_translated_path if (tcfg.tts_source == TTS_SOURCES[0] and cues_tr) else item.srt_path
            src_cues = rp.remap_cues_for_trim(src_cues, trim)
            fit = trim.kept_duration if trim.active else info.duration   # track giọng tính theo thời gian nguồn (atempo theo tốc độ sau)
            voice_path = self.generate_voice(item, src_cues, srt_path=src_path or "", extra_key=trim_key,
                                             progress=stage("Tạo giọng đọc", 0.45, 0.2), log=log, cancel=cancel,
                                             fit_duration=fit if not vcfg.loop_to_voice else None)
            on_update(item)

        job_dir = self.job_dir(item)
        job_dir.mkdir(parents=True, exist_ok=True)
        ass_name: str | None = None
        if sub_on and cues_orig:
            sub_cues = select_sub_cues(scfg, cues_orig, cues_tr)
            sub_cues = rp.remap_cues_for_trim(sub_cues, trim)
            synced_srt = voice_path.with_suffix(".srt") if voice_path else None
            same_source = tts_on and ((tcfg.tts_source == TTS_SOURCES[0] and scfg.srt_mode == SRT_MODES[1]) or
                                      (tcfg.tts_source == TTS_SOURCES[1] and scfg.srt_mode == SRT_MODES[0]))
            if synced_srt and synced_srt.exists() and same_source:
                sub_cues = shift(load_srt(synced_srt), scfg.sub_delay)
                log("Phụ đề dùng mốc thời gian đã đồng bộ theo giọng đọc.")
            sub_cues = rp.scale_cues(sub_cues, 100.0 / max(25, vcfg.speed))
            geo = rp.output_geometry(mods.info, vcfg)
            write_ass(sub_cues, scfg, geo.width, geo.height, job_dir / "sub.ass")
            ass_name = "sub.ass"

        out_dir = Path(st.output_dir) if st.output_dir else VOICE_DIR.parent / "videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = ".mp3" if self.audio_only else ".mp4"
        out_path = out_dir / f"{item.stem}{vcfg.output_suffix}{suffix}{ext}"
        voice_dur = self.ffmpeg.duration_of(voice_path) if voice_path else 0.0
        audio_override: Path | None = None
        if vcfg.demucs_enable and vcfg.keep_original_audio and not vcfg.mute and info.has_audio:
            from app.services.demucs_sep import separate_no_vocals

            item.message = "Tách lời thoại (Demucs)"
            on_update(item)
            audio_override = separate_no_vocals(item.path, self.ffmpeg, log=log, cancel=cancel)
            if audio_override is not None and trim.active:
                # cắt track thay thế theo đúng các đoạn giữ lại
                parts = []
                for i, (a, b) in enumerate(trim.segments):
                    parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[s{i}]")
                labels = "".join(f"[s{i}]" for i in range(len(trim.segments)))
                graph = ";".join(parts) + f";{labels}concat=n={len(trim.segments)}:v=0:a=1[a]"
                trimmed = job_dir / "override_trim.wav"
                res = self.ffmpeg.run_simple(["-i", str(audio_override), "-filter_complex", graph, "-map", "[a]",
                                              str(trimmed)], timeout=600)
                audio_override = trimmed if res.returncode == 0 and trimmed.exists() else None
        item.message = "Ghép timeline"
        on_update(item)
        plan = rp.plan_render(item, mods.info, vcfg, ffmpeg=self.ffmpeg, job_dir=job_dir, out_path=out_path,
                              ass_name=ass_name, voice_path=voice_path, stages=mods.stages,
                              pre_stages=mods.pre_stages, src_stages=mods.src_stages, trim=trim, audio_extras=mods.audio_extras,
                              voice_duration=voice_dur, audio_only=self.audio_only, audio_override=audio_override,
                              audio_orig_filters=mods.audio_orig_filters, log=log)
        log(f"Render → {out_path.name}")
        rp.run_plan(plan, self.ffmpeg, on_progress=stage("Render", 0.65, 0.35), cancel=cancel)
        item.output_path = str(out_path)
        if final:
            item.status, item.progress, item.message = "Xong", 1.0, out_path.name
        else:
            item.progress, item.message = 1.0, out_path.name
        on_update(item)
        return out_path


class JobRunner:
    """Chạy hàng đợi trong thread nền; mỗi lúc chỉ một hàng đợi."""

    def __init__(self, pipeline: Pipeline, ui_call: Callable[[Callable[[], None]], None]) -> None:
        self.pipeline = pipeline
        self.ui_call = ui_call
        self._thread: threading.Thread | None = None
        self.cancel = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, items: list[VideoItem], *, on_update: Callable[[VideoItem], None],
              on_log: LogCb, on_done: Callable[[int, int], None]) -> bool:
        if self.running or not items:
            return False
        self.cancel.clear()

        def _ui_update(it: VideoItem) -> None:
            self.ui_call(lambda: on_update(it))

        def _ui_log(msg: str) -> None:
            logger.info(msg)
            self.ui_call(lambda: on_log(msg))

        def run() -> None:
            ok = fail = 0
            for it in items:
                if self.cancel.is_set():
                    it.status, it.message = "Đã huỷ", ""
                    _ui_update(it)
                    continue
                try:
                    self.pipeline.process(it, on_update=_ui_update, log=_ui_log, cancel=self.cancel)
                    ok += 1
                except RenderCancelled:
                    it.status, it.message = "Đã huỷ", ""
                    _ui_update(it)
                except Exception as exc:
                    logger.exception("Lỗi xử lý %s", it.name)
                    it.status, it.message = "Lỗi", str(exc).splitlines()[0][:160]
                    _ui_update(it)
                    _ui_log(f"✖ {it.name}: {exc}")
                    fail += 1
            self.ui_call(lambda: on_done(ok, fail))

        self._thread = threading.Thread(target=run, name="JobRunner", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self.cancel.set()


class TaskRunner:
    """Tác vụ lẻ (nhận diện/dịch/tạo giọng cho video đang chọn)."""

    def __init__(self, ui_call: Callable[[Callable[[], None]], None]) -> None:
        self.ui_call = ui_call
        self._thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.name = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, name: str, fn: Callable[[threading.Event], object], *,
              on_done: Callable[[object | None, Exception | None], None]) -> bool:
        if self.running:
            return False
        self.cancel.clear()
        self.name = name

        def run() -> None:
            result: object | None = None
            error: Exception | None = None
            try:
                result = fn(self.cancel)
            except Exception as exc:
                if not isinstance(exc, RenderCancelled):
                    logger.exception("Tác vụ %s lỗi", name)
                error = exc
            self.ui_call(lambda: on_done(result, error))

        self._thread = threading.Thread(target=run, name=f"Task-{name}", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self.cancel.set()
