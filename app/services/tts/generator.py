"""Từ danh sách cue → một track WAV lồng tiếng khớp thời gian.

Quy trình:
1. Tổng hợp từng cue (có cache theo nội dung + giọng + tham số).
2. Giải mã về PCM; cue nào dài hơn khe thời gian thì tăng tốc (atempo) tới mức
   ``max_tempo``; vẫn tràn thì cue sau bị đẩy lùi một chút thay vì chồng tiếng.
3. Ghi tuần tự vào WAV 24 kHz mono — không giữ cả track trong RAM.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

from app.config.paths import TTS_CACHE_DIR
from app.core.state import Settings, TTSConfig
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue
from app.services.tts.base import TTSEngine

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
_BYTES_PER_SEC = SAMPLE_RATE * 2   # s16 mono


@dataclass
class VoiceTrackResult:
    wav_path: Path
    cue_count: int
    sped_up: int
    overflow: int
    duration: float
    synced_cues: list[Cue] | None = None     # cue đã dời theo vị trí thật của giọng (natural_sync)


def make_engine(cfg: TTSConfig, settings: Settings) -> TTSEngine:
    if cfg.tts_provider == "ElevenLabs":
        from app.services.tts.elevenlabs import ElevenLabsTTS

        extra = settings.split_keys(settings.elevenlabs_api_keys)
        return ElevenLabsTTS(settings.elevenlabs_api_key or (extra[0] if extra else ""), model_id=cfg.eleven_model,
                             stability=cfg.eleven_stability, similarity=cfg.eleven_similarity, style=cfg.eleven_style,
                             speaker_boost=cfg.eleven_speaker_boost, extra_keys=extra)
    if cfg.tts_provider == "Google Cloud TTS":
        from app.services.tts.google import GoogleTTS

        return GoogleTTS(settings.google_keys(), sa_json=settings.google_sa_json)
    if cfg.tts_provider.startswith("VieNeu"):
        from app.services.tts.vieneu import VieNeuTTS

        return VieNeuTTS(precision=settings.vieneu_precision)
    if cfg.tts_provider.startswith("Gemini"):
        from app.services.tts.gemini_tts import GeminiTTS

        return GeminiTTS(settings.gemini_keys(), model=cfg.gemini_tts_model, style=cfg.gemini_style,
                         custom_style=cfg.gemini_style_custom)
    if cfg.tts_provider.startswith("FPT"):
        from app.services.tts.fpt import FptTTS

        return FptTTS(settings.split_keys(settings.fpt_api_key))
    from app.services.tts.edge import EdgeTTS

    return EdgeTTS()


def uses_language_list(provider: str) -> bool:
    """Nhà cung cấp chia giọng theo ngôn ngữ (Edge, Google Cloud) → có ô «Ngôn ngữ»; còn lại chọn thẳng giọng."""
    return provider in ("Edge TTS", "Google Cloud TTS")


def _voice_lang(cfg: TTSConfig, voice_id: str) -> str:
    """Mã ngôn ngữ của giọng đang chọn (vi, zh, ja, ko, en…) — '' nếu không rõ (ElevenLabs đa ngôn ngữ)."""
    if cfg.tts_provider == "Edge TTS":
        if "Multilingual" in voice_id:          # giọng đa ngôn ngữ đọc được mọi thứ tiếng
            return ""
        return (voice_id.split("-")[0] if "-" in voice_id else cfg.edge_language.split("-")[0]).lower()
    if cfg.tts_provider == "Google Cloud TTS":
        return voice_id.split("-")[0].lower() if "-" in voice_id else ""
    if cfg.tts_provider.startswith("VieNeu") or cfg.tts_provider.startswith("FPT"):
        return "vi"
    return ""


def _check_script_matches_voice(items, cfg: TTSConfig, voice_id: str) -> None:
    """Chữ Trung/Nhật/Hàn mà giọng không phải zh/ja/ko → Edge trả «No audio», VieNeu đọc sai: dừng ngay, chỉ cách sửa."""
    from app.services.srt_utils import is_cjk

    lang = _voice_lang(cfg, voice_id)
    if not lang or lang in ("zh", "ja", "ko"):
        return
    cjk = sum(1 for _i, _c, t in items if is_cjk(t))
    if cjk >= max(3, len(items) * 0.4):
        raise RuntimeError(
            f"Phụ đề đang là chữ Trung/Nhật/Hàn ({cjk}/{len(items)} câu) nhưng giọng đọc là tiếng '{lang}' — giọng không đọc "
            "được. Bật «Dịch AI» ở mục SRT rồi ở mục TTS chọn «Sub đã dịch», hoặc chọn giọng đúng ngôn ngữ của phụ đề.")


def voice_id_for(cfg: TTSConfig) -> str:
    if cfg.tts_provider == "ElevenLabs":
        return cfg.eleven_voice_id
    if cfg.tts_provider == "Google Cloud TTS":
        return cfg.google_voice
    if cfg.tts_provider.startswith("VieNeu"):
        return cfg.vieneu_voice
    if cfg.tts_provider.startswith("Gemini"):
        return cfg.gemini_voice
    if cfg.tts_provider.startswith("FPT"):
        return cfg.fpt_voice
    return cfg.edge_voice


def config_with_voice(cfg: TTSConfig, provider: str, voice_id: str, locale: str = "", speed: float = 0.0,
                      pitch: int | None = None) -> TTSConfig:
    """Bản sao ``cfg`` đổi sang giọng khác (giữ các tham số khớp thời gian, ElevenLabs…)."""
    c = replace(cfg)
    c.tts_provider = provider or cfg.tts_provider
    c.set_voice(voice_id)
    if locale:
        c.edge_language = c.google_language = locale
    if speed:
        c.tts_speed = speed
    if pitch is not None:
        c.tts_pitch = pitch
    return c


def speaker_configs(cfg: TTSConfig) -> dict[str, TTSConfig]:
    """Người nói → cấu hình giọng riêng (chỉ khi bật «Nhiều giọng»). Người chưa chọn giọng dùng giọng chính."""
    out: dict[str, TTSConfig] = {}
    if not cfg.multi_voice:
        return out
    for sv in cfg.speaker_voices or []:
        if not isinstance(sv, dict) or not sv.get("name") or not sv.get("voice_id"):
            continue
        try:
            speed = float(sv.get("speed") or 0)
            pitch = int(sv["pitch"]) if sv.get("pitch") is not None else None
        except (TypeError, ValueError):
            speed, pitch = 0.0, None
        out[str(sv["name"])] = config_with_voice(cfg, str(sv.get("provider") or ""), str(sv["voice_id"]),
                                                 str(sv.get("locale") or ""), speed, pitch)
    return out


def cache_path(engine: TTSEngine, voice_id: str, text: str, speed: float, pitch: int) -> Path:
    key = hashlib.sha1(f"{engine.cache_signature(voice_id, speed, pitch)}|{text}".encode("utf-8")).hexdigest()
    return TTS_CACHE_DIR / engine.name / f"{key}{getattr(engine, 'ext', '.mp3')}"


def synthesize_cached(engine: TTSEngine, voice_id: str, text: str, *, speed: float, pitch: int) -> Path:
    p = cache_path(engine, voice_id, text, speed, pitch)
    if p.exists() and p.stat().st_size > 0:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    out = engine.synthesize(text, voice_id, p, speed=speed, pitch=pitch)
    return Path(out) if out else p


def generate_voice_track(cues: list[Cue], cfg: TTSConfig, settings: Settings, ffmpeg: FFmpeg, out_wav: Path, *,
                         on_progress: Callable[[float], None] | None = None,
                         log: Callable[[str], None] | None = None,
                         cancel: threading.Event | None = None,
                         fit_duration: float | None = None) -> VoiceTrackResult:
    """``fit_duration``: thời lượng video (đã cắt). Chế độ mềm sẽ tự nâng dần mức tăng tốc (1.10 → max_tempo)
    tới khi track giọng không dài quá video — giọng tự nhiên nhất có thể mà không bị cắt cụt ở cuối."""
    engine = make_engine(cfg, settings)
    voice_id = voice_id_for(cfg)
    items = [(i, c, c.plain) for i, c in enumerate(cues) if c.plain]
    if not items:
        raise RuntimeError("Phụ đề trống — không có gì để đọc.")

    # giọng cho từng câu: nhiều giọng theo người nói (meta["speaker"]), còn lại giọng chính
    spk_cfgs = speaker_configs(cfg)
    engines: dict[str, TTSEngine] = {cfg.tts_provider: engine}
    plan: dict[int, tuple[TTSEngine, str, float, int]] = {}      # cue → (engine, giọng, tốc độ, cao độ)
    used: dict[str, int] = {}
    for i, c, _t in items:
        sc = spk_cfgs.get(str(c.meta.get("speaker", "")), cfg)
        if sc.tts_provider not in engines:
            engines[sc.tts_provider] = make_engine(sc, settings)
        eng = engines[sc.tts_provider]
        pitch_i = sc.tts_pitch if sc.tts_provider == "Edge TTS" else 0
        vid = voice_id_for(sc)
        plan[i] = (eng, vid, sc.tts_speed, pitch_i)
        key = f"{vid}{f' {pitch_i:+d}Hz' if pitch_i else ''}"
        used[key] = used.get(key, 0) + 1
    _check_script_matches_voice(items, cfg, voice_id)

    def synth(i: int, text: str) -> Path:
        eng, vid, sp, pt = plan[i]
        return synthesize_cached(eng, vid, text, speed=sp, pitch=pt)

    def residual_of(i: int) -> float:
        eng, _vid, sp, _pt = plan[i]
        return eng.residual_tempo(sp)

    def _check() -> None:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ tạo giọng")

    # ── 1. Tổng hợp ───────────────────────────────────────────────────────
    paths: dict[int, Path] = {}
    workers = max(1, min(engine.workers, settings.tts_workers or engine.workers))
    if log:
        log(f"Tổng hợp {len(items)} cue bằng {cfg.tts_provider} ({workers} luồng)…")
        if len(used) > 1:
            log(f"Nhiều giọng: {len(used)} giọng — " + ", ".join(f"{k} ({n} câu)" for k, n in used.items()))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(synth, i, text): i for i, _, text in items}
        failed: list[int] = []
        for fut in as_completed(futs):
            _check()
            i = futs[fut]
            try:
                paths[i] = fut.result()
            except Exception as exc:          # 1 câu lỗi (mạng/dịch vụ) → để trống câu đó, không hỏng cả job
                failed.append(i)
                logger.warning("TTS cue %d lỗi: %s", i + 1, exc)
            done += 1
            if on_progress:
                on_progress(0.65 * done / len(items))
    if failed:
        # lỗi thường do dịch vụ quá tải khi gọi song song → thử lại tuần tự một lượt trước khi bỏ
        still: list[int] = []
        for i in failed:
            _check()
            text = next(t for j, _c, t in items if j == i)
            try:
                paths[i] = synth(i, text)
            except Exception as exc:
                logger.warning("TTS cue %d lỗi lần 2: %s", i + 1, exc)
                still.append(i)
        failed = still
    if failed:
        if len(failed) == len(items):
            raise RuntimeError("Không tổng hợp được câu nào — kiểm tra mạng / dịch vụ TTS.")
        if log:
            log(f"⚠ {len(failed)} câu không tổng hợp được (bỏ trống): {', '.join(str(i + 1) for i in failed[:8])}"
                + ("…" if len(failed) > 8 else ""))
        items = [it for it in items if it[0] not in failed]

    # ── 2. Giải mã + khớp thời gian ───────────────────────────────────────
    order = [i for i, _, _ in items]
    by_index = {i: c for i, c, _ in items}

    def slot_for(pos: int) -> float:
        cue = by_index[order[pos]]
        if pos + 1 < len(order):
            nxt = by_index[order[pos + 1]]
            return max(0.35, nxt.start - cue.start - cfg.min_gap)
        return max(cue.duration, 0.35)

    if log:
        log("Khớp thời gian và ghép track…")

    def natural(pos: int) -> tuple[int, bytes]:
        _check()
        return pos, ffmpeg.decode_pcm(paths[order[pos]], sample_rate=SAMPLE_RATE, tempo=residual_of(order[pos]))

    base: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, fut in enumerate(as_completed([pool.submit(natural, pos) for pos in range(len(order))])):
            pos, pcm = fut.result()
            base[pos] = pcm
            if on_progress:
                on_progress(0.65 + 0.2 * (n + 1) / len(order))
    nat_dur = {pos: len(base[pos]) / _BYTES_PER_SEC for pos in base}

    def tempo_for(pos: int, cap: float) -> float:
        if cfg.natural_sync:
            return 1.0
        slot = slot_for(pos)
        d = nat_dur[pos]
        if d > slot + 0.03:
            t = min(cap, d / slot)
            return t if t > 1.02 else 1.0
        return 1.0

    def simulate(cap: float) -> float:
        """Độ dài track nếu áp mức tăng tốc tối đa ``cap`` (không cần giải mã lại)."""
        cursor = 0.0
        for pos in range(len(order)):
            cue = by_index[order[pos]]
            d = nat_dur[pos] / tempo_for(pos, cap)
            start = max(cue.start, cursor)
            if (cfg.natural_sync or cfg.soft_timing) and pos > 0:
                start = max(start, cursor + cfg.min_gap)
            cursor = start + d
        return cursor

    cap = 1.10 if cfg.soft_timing else cfg.max_tempo
    if cfg.soft_timing and fit_duration and fit_duration > 0:
        # nâng dần mức tăng tốc tới khi track không dài quá video (tối đa max_tempo)
        ladder = [round(1.10 + 0.05 * k, 2) for k in range(0, 40)]
        for c in ladder:
            if c > cfg.max_tempo:
                break
            cap = c
            if simulate(c) <= fit_duration + 0.3:
                break
        if log and cap > 1.10:
            log(f"Khớp mềm: nâng tốc độ tối đa lên ×{cap:.2f} để giọng không dài quá video")

    def decode(pos: int) -> tuple[int, bytes, bool, bool]:
        _check()
        idx = order[pos]
        tempo = tempo_for(pos, cap)
        pcm = base[pos]
        sped = False
        if tempo > 1.0:
            pcm = ffmpeg.decode_pcm(paths[idx], sample_rate=SAMPLE_RATE, tempo=residual_of(idx) * tempo)
            sped = True
        dur = len(pcm) / _BYTES_PER_SEC
        overflow = (not cfg.natural_sync) and dur > slot_for(pos) + 0.05
        return pos, pcm, sped, overflow

    decoded: dict[int, tuple[bytes, bool, bool]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs2 = [pool.submit(decode, pos) for pos in range(len(order))]
        for n, fut in enumerate(as_completed(futs2)):
            pos, pcm, sped, overflow = fut.result()
            decoded[pos] = (pcm, sped, overflow)
            if on_progress:
                on_progress(0.85 + 0.1 * (n + 1) / len(order))
    base.clear()

    # ── 3. Ghi WAV tuần tự ────────────────────────────────────────────────
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_wav.with_suffix(".part.wav")
    cursor = 0.0
    sped_up = overflow_n = 0
    synced: list[Cue] = []
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for pos in range(len(order)):
            _check()
            cue = by_index[order[pos]]
            pcm, sped, overflow = decoded[pos]
            sped_up += int(sped)
            overflow_n += int(overflow)
            start = max(cue.start, cursor)
            if (cfg.natural_sync or cfg.soft_timing) and pos > 0:
                start = max(start, cursor + cfg.min_gap)
            gap = start - cursor
            if gap > 0:
                wf.writeframes(b"\x00" * (int(gap * SAMPLE_RATE) * 2))
            wf.writeframes(pcm)
            dur = len(pcm) / _BYTES_PER_SEC
            synced.append(Cue(len(synced) + 1, start, start + max(0.3, dur), cue.text, dict(cue.meta)))
            cursor = start + dur
    tmp.replace(out_wav)
    if on_progress:
        on_progress(1.0)
    if log:
        log(f"Track giọng: {cursor:.1f}s · {sped_up} cue tăng tốc · {overflow_n} cue vẫn tràn"
            + (" · phụ đề dời theo giọng" if (cfg.natural_sync or cfg.soft_timing) else ""))
        if fit_duration and cursor > fit_duration + 0.5:
            log(f"⚠ Giọng dài hơn video {cursor - fit_duration:.1f}s dù đã tăng tốc tối đa ×{cfg.max_tempo:.2f} — "
                "phần cuối sẽ bị cắt. Rút gọn bản dịch, tăng «Tốc độ tối đa», hoặc bật «Lặp video cho đủ giọng».")
    return VoiceTrackResult(out_wav, len(items), sped_up, overflow_n, cursor,
                            synced if (cfg.natural_sync or cfg.soft_timing) else None)


def preview_text(locale: str) -> str:
    loc = (locale or "").lower()
    if loc.startswith("vi"):
        return "Xin chào, đây là giọng đọc thử của Edit Studio. Chúc bạn một ngày tốt lành."
    if loc.startswith("ja"):
        return "こんにちは。これは Edit Studio の音声サンプルです。"
    if loc.startswith("ko"):
        return "안녕하세요. Edit Studio 음성 샘플입니다."
    if loc.startswith("zh") or loc.startswith("cmn"):
        return "你好，这是 Edit Studio 的语音示例。"
    return "Hello, this is a voice sample from Edit Studio. Have a great day."
