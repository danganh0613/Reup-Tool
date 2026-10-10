"""Từ danh sách cue → một track WAV lồng tiếng khớp thời gian.

Quy trình:
1. Chuẩn hoá chữ, tổng hợp từng cue (có cache theo nội dung + giọng + tham số); câu không đạt kiểm tra chất lượng
   thì đọc lại (``quality``); câu lỗi hẳn thì đọc bằng engine dự phòng.
2. Giải mã về PCM, hậu kỳ từng câu (``postprocess``: cắt «rẹt» cuối câu, cắt lặng, fade, cân độ to); khớp mềm: cue
   nào dài hơn khe thời gian thì tăng tốc (atempo) — một lần duy nhất, tổng cùng tốc độ đọc ≤ 1,25×; vẫn tràn thì cue
   sau dời vào khoảng lặng / đọc nhanh hơn để đuổi kịp hình, và câu đó được báo để rút gọn bản dịch.
3. Ghi tuần tự vào WAV 24 kHz mono theo mốc của video RA (đã đổi tốc độ) — không giữ cả track trong RAM.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.config.paths import PRON_FILE, TTS_CACHE_DIR
from app.core.state import Settings, TTSConfig
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, ensure_min_duration
from app.services.tts import batch, postprocess, quality
from app.services.tts.base import TTSEngine
from app.services.tts.normalize import is_vietnamese, normalize_vi_text, split_long

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
_BYTES_PER_SEC = SAMPLE_RATE * 2   # s16 mono
TRIM_VERSION = "trim3"             # đổi khi đổi cách xử lý từng câu → track giọng cũ được tạo lại
PULL_EARLY = 0.2                   # chế độ mềm: câu được vào sớm tối đa 0,2 s khi câu trước đã đọc xong
CATCH_UP_LATE = 0.15               # chế độ mềm: câu bắt đầu trễ hơn mốc phụ đề quá ngần này → nén thêm để đuổi kịp hình
PART_GAP = 0.25                    # nghỉ giữa các đoạn của một câu quá dài được tách ra đọc riêng
MAX_TOTAL_TEMPO = 1.25             # time-stretch tổng (tốc độ đọc × nén cho vừa khung) — quá mức này giọng méo, nuốt chữ
MIN_SUB = 1.0                      # phụ đề dời theo giọng hiện tối thiểu 1 s


@dataclass
class QCOptions:
    """Kiểm chất lượng khi đọc: số lần đọc lại tối đa, có cho Whisper nghe lại không."""
    retries: int = 3
    asr: bool = False
    compute: str = "auto"
    reread: list[str] = field(default_factory=list)     # câu đã phải đọc lại trong lượt này (để báo log)


def qc_options(settings: Settings | None = None) -> QCOptions:
    """Kiểm từng câu (luôn bật): đọc lại tối đa 3 lần; Whisper nghe lại khi máy có GPU + model Whisper sẵn."""
    return QCOptions(3, quality.asr_available())


def post_signature(settings: Settings) -> str:
    """Tham số hậu kỳ có ảnh hưởng tới track giọng — đổi thì track được tạo lại."""
    try:
        pron = int(PRON_FILE.stat().st_mtime)
    except OSError:
        pron = 0
    # «True|auto»: kiểm lỗi + Whisper nghe lại tự động (trước là tuỳ chọn) — giữ nguyên chữ ký để track cũ dùng lại được
    return (f"{TRIM_VERSION}|{settings.tts_fade_in_ms}|{settings.tts_fade_out_ms}|{settings.tts_level_db}|"
            f"True|auto|{pron}")


def polisher(settings: Settings | None) -> Callable[[bytes], bytes]:
    """Hậu kỳ một câu PCM 24 kHz theo Cài đặt: cắt «rẹt» cuối câu, cắt lặng, fade vào/ra, cân độ to."""
    s = settings or Settings()
    level = s.tts_level_db if s.tts_level_db < 0 else None

    def run(pcm: bytes) -> bytes:
        return postprocess.polish(pcm, SAMPLE_RATE, fade_in=max(0, s.tts_fade_in_ms) / 1000,
                                  fade_out=max(0, s.tts_fade_out_ms) / 1000, level_db=level)
    return run


@dataclass
class VoiceTrackResult:
    wav_path: Path
    cue_count: int
    sped_up: int
    overflow: int
    duration: float
    synced_cues: list[Cue] | None = None     # cue đã dời theo vị trí thật của giọng
    overflow_cues: list[tuple[int, float, float]] = field(default_factory=list)  # (chỉ số cue gốc, giây tràn, khung)
    report: list[dict] = field(default_factory=list)                            # từng câu: engine, tempo, số lần đọc…


def make_engine(cfg: TTSConfig, settings: Settings) -> TTSEngine:
    if cfg.tts_provider.startswith("VieNeu"):
        from app.services.tts.vieneu import VieNeuTTS

        return VieNeuTTS(precision=settings.vieneu_precision)
    from app.services.tts.edge import EdgeTTS

    return EdgeTTS()


def uses_language_list(provider: str) -> bool:
    """Edge chia giọng theo ngôn ngữ → có ô «Ngôn ngữ»; VieNeu chọn thẳng giọng."""
    return provider == "Edge TTS"


def _voice_lang(cfg: TTSConfig, voice_id: str) -> str:
    """Mã ngôn ngữ của giọng đang chọn (vi, zh, ja, ko, en…) — '' nếu đọc được mọi thứ tiếng."""
    if cfg.tts_provider == "Edge TTS":
        if "Multilingual" in voice_id:          # giọng đa ngôn ngữ đọc được mọi thứ tiếng
            return ""
        return (voice_id.split("-")[0] if "-" in voice_id else cfg.edge_language.split("-")[0]).lower()
    return "vi" if cfg.tts_provider.startswith("VieNeu") else ""


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
    return cfg.vieneu_voice if cfg.tts_provider.startswith("VieNeu") else cfg.edge_voice


def main_locale(cfg: TTSConfig) -> str:
    """Ngôn ngữ của giọng chính (để gợi ý giọng người nói cùng ngôn ngữ)."""
    return cfg.edge_language if cfg.tts_provider == "Edge TTS" else "vi-VN"


def config_with_voice(cfg: TTSConfig, provider: str, voice_id: str, locale: str = "", speed: float = 0.0,
                      pitch: int | None = None) -> TTSConfig:
    """Bản sao ``cfg`` đổi sang giọng khác (giữ các tham số khớp thời gian…)."""
    c = replace(cfg)
    c.tts_provider = provider or cfg.tts_provider
    c.set_voice(voice_id)
    if locale:
        c.edge_language = locale
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
        prov = str(sv.get("provider") or "")
        out[str(sv["name"])] = config_with_voice(cfg, prov, str(sv["voice_id"]), str(sv.get("locale") or ""), speed, pitch)
    return out


def cache_path(engine: TTSEngine, voice_id: str, text: str, speed: float, pitch: int) -> Path:
    key = hashlib.sha1(f"{engine.cache_signature(voice_id, speed, pitch)}|{text}".encode("utf-8")).hexdigest()
    return TTS_CACHE_DIR / engine.name / f"{key}{getattr(engine, 'ext', '.mp3')}"


def _try_path(p: Path, k: int) -> Path:
    """File tạm cho lần đọc thứ ``k`` — riêng từng luồng (hai cue cùng chữ chạy song song không đè nhau)."""
    return p.with_name(f"{p.stem}.t{k}.{os.getpid()}-{threading.get_ident()}{p.suffix}")


def _rank(c: tuple[Path, quality.Verdict | None]) -> tuple[int, float]:
    v = c[1]
    return (v.score, abs(v.speech - v.expected)) if v else (0, 0.0)


def synthesize_cached(engine: TTSEngine, voice_id: str, text: str, *, speed: float, pitch: int,
                      qc: QCOptions | None = None, label: str = "") -> Path:
    """Đọc một câu (có cache). Chữ được chuẩn hoá trước. File WAV trong cache đã làm sạch (cắt «rẹt» cuối câu, fade).
    ``qc`` bật → kiểm chất lượng (mọi engine; MP3 giải mã để kiểm); câu hỏng thì đọc lại (tối đa
    ``engine.qc_retries`` lần), giữ bản tốt nhất — kể cả file cache có từ trước. ``label`` (vd «câu 12») để ghi log."""
    text = normalize_vi_text(text)
    if engine.max_chars and len(text) > engine.max_chars:
        parts = split_long(text, engine.max_chars)
        if len(parts) > 1:
            return _synthesize_parts(engine, voice_id, text, parts, speed=speed, pitch=pitch, qc=qc)
    p = cache_path(engine, voice_id, text, speed, pitch)
    wav = getattr(engine, "ext", ".mp3") == ".wav"           # chỉ làm sạch WAV — MP3 sửa là phải nén lại lần nữa
    check = qc is not None
    asr = bool(check and qc.asr)
    budget = min(qc.retries, engine.qc_retries) if check else 0
    tag = f"TTS {label}" if label else "TTS"

    def verdict(path: Path) -> quality.Verdict | None:
        return quality.check_file(path, text, speed=engine.clamp_speed(speed), asr=asr,
                                  compute=qc.compute) if check else None

    def good(v: quality.Verdict | None) -> bool:
        """Không cần đọc lại: đạt, hoặc chỉ lỗi nhẹ mà engine tốn lượt gọi (Gemini) — đọc lại hiếm khi khá hơn."""
        return v is None or v.ok or (not engine.reread_soft and v.score < quality.HARD)

    cands: list[tuple[Path, quality.Verdict | None]] = []
    if p.exists() and p.stat().st_size > 0:
        m = quality.read_mark(p)
        if m and (m.get("qc") or not check) and (m.get("asr") or not asr):
            return p
        if not wav and not check:
            return p
        engine.repair_cached(p)
        v = verdict(p)
        if good(v) or budget == 0:
            if wav:
                postprocess.clean_file(p)
            quality.mark(p, v, qc=check, asr=asr, tries=int((m or {}).get("tries", 1)))
            return p
        old = _try_path(p, 0)                          # bản cũ chưa đạt → vẫn được so cùng các lần đọc mới
        os.replace(p, old)
        cands.append((old, v))
        logger.info("%s: «%s» trong cache chưa đạt (%s) → đọc lại", tag, text[:50], "; ".join(v.problems))
    p.parent.mkdir(parents=True, exist_ok=True)
    for k in range(1, budget + (1 if not cands else 0) + 1):
        try:
            out = engine.synthesize(text, voice_id, _try_path(p, k), speed=speed, pitch=pitch)
        except Exception:
            if not cands:
                raise
            logger.warning("%s: đọc lại «%s» lỗi — giữ bản tốt nhất đã có", tag, text[:50], exc_info=True)
            break
        out = Path(out) if out else _try_path(p, k)
        v = verdict(out)
        cands.append((out, v))
        if good(v):
            break
        logger.info("%s: «%s» lần %d chưa đạt: %s", tag, text[:50], len(cands), "; ".join(v.problems))
    if check and len(cands) > 1 and qc is not None:
        qc.reread.append(text)
    best, bv = min(cands, key=_rank)
    for path, _v in cands:
        if path != best:
            path.unlink(missing_ok=True)
    if wav:
        postprocess.clean_file(best)
    os.replace(best, p)
    quality.mark(p, bv, qc=check, asr=asr, tries=len(cands))
    return p


def _synthesize_parts(engine: TTSEngine, voice_id: str, text: str, parts: list[str], *, speed: float, pitch: int,
                      qc: QCOptions | None) -> Path:
    """Câu quá dài với engine kiểu LLM (Gemini): đọc từng đoạn — mỗi đoạn có cache + kiểm riêng — rồi ghép."""
    p = cache_path(engine, voice_id, text, speed, pitch)
    if p.exists() and p.stat().st_size > 0:
        return p
    pcms: list[bytes] = []
    rate = 0
    for part in parts:
        r = postprocess.read_wav_mono16(synthesize_cached(engine, voice_id, part, speed=speed, pitch=pitch, qc=qc))
        if r is None or (rate and r[1] != rate):
            raise RuntimeError("Không ghép được các đoạn của câu dài (định dạng âm thanh khác nhau).")
        rate = r[1]
        pcms.append(postprocess.polish(r[0], rate))
    p.parent.mkdir(parents=True, exist_ok=True)
    postprocess.write_wav(p, (b"\x00\x00" * int(PART_GAP * rate)).join(pcms), rate)
    quality.mark(p, None, qc=qc is not None, asr=bool(qc and qc.asr))
    return p


def prefill_batches(jobs: list[tuple[TTSEngine, str, float, int, str]], settings: Settings, qc: QCOptions | None, *,
                    log: Callable[[str], None] | None = None, check: Callable[[], None] | None = None,
                    on_progress: Callable[[float], None] | None = None) -> bool:
    """``jobs``: (engine, giọng, tốc độ, cao độ, chữ) theo thứ tự đọc. Engine đọc gộp được: câu chưa có trong cache →
    gộp lô, đọc MỘT lượt, tách ra file cache từng câu (tts/batch.py). Engine gộp mọi câu cùng giọng: lô lớn — né giới hạn
    lượt gọi mỗi phút. VieNeu (``batch_adjacent``): chỉ gộp vài câu KỀ NHAU cùng giọng — câu ngắn đọc có ngữ cảnh.
    Câu dài hơn ``max_chars`` (lời kể Dựng truyện…) gộp theo từng đoạn — đúng các đoạn ``_synthesize_parts`` sẽ đọc.
    File tách ra vẫn qua bước kiểm chất lượng như câu đọc riêng; câu tách không chắc / lô lỗi → đọc từng câu như cũ.
    Trả True nếu đã đọc gộp ít nhất một lô."""
    groups: dict[tuple, list[str]] = {}
    engs: dict[tuple, TTSEngine] = {}
    seen: set[Path] = set()
    run, prev = 0, None
    for eng, vid, sp, pt, text in jobs:
        t = normalize_vi_text(text)
        key = (id(eng), vid, sp, pt)
        if key != prev:                          # đổi giọng / người nói → đoạn đọc liền mới
            run, prev = run + 1, key
        if not eng.batch_lines or not t:
            continue
        parts = split_long(t, eng.max_chars) if eng.max_chars and len(t) > eng.max_chars else [t]
        if len(parts) > 1:
            whole = cache_path(eng, vid, t, sp, pt)
            if whole.exists() and whole.stat().st_size > 0:
                continue
            parts = [normalize_vi_text(x) for x in parts]
        for part in parts:
            p = cache_path(eng, vid, part, sp, pt)
            if p in seen or (p.exists() and p.stat().st_size > 0):     # câu trùng chữ / đã có trong cache
                continue
            seen.add(p)
            gkey = (*key, run if eng.batch_adjacent else 0)
            engs[gkey] = eng
            groups.setdefault(gkey, []).append(part)
    lots = [(key, lot) for key, texts in groups.items()
            for lot in batch.chunks(texts, engs[key].batch_lines, engs[key].batch_chars) if len(lot) >= batch.MIN_LINES]
    if not lots:
        return False
    if not batch.available():
        if log:
            log("Máy chưa có Whisper → không tách được đoạn đọc gộp, đọc từng câu như cũ")
        return False
    total = sum(len(lot) for _k, lot in lots)
    if log:
        log(f"Đọc gộp: {total} câu → {len(lots)} đoạn đọc liền rồi tách lại từng câu…")
    compute = qc.compute if qc else "auto"
    ok = 0
    for n, (key, lot) in enumerate(lots):
        if check:
            check()
        eng, (vid, sp, pt) = engs[key], key[1:4]
        try:
            pcm, rate = eng.synthesize_batch(lot, vid)
            parts = batch.split(pcm, rate, lot, compute)
        except RenderCancelled:
            raise
        except Exception as exc:
            logger.warning("Đọc gộp lô %d lỗi: %s", n + 1, exc)
            if log:
                log(f"⚠ Đọc gộp lô {n + 1} lỗi ({str(exc)[:80]}) → các câu đó đọc riêng")
            continue
        for t, seg in zip(lot, parts):
            if seg:
                p = cache_path(eng, vid, t, sp, pt)
                p.parent.mkdir(parents=True, exist_ok=True)
                postprocess.write_wav(p, seg, rate)
                ok += 1
        if on_progress:
            on_progress(0.3 * (n + 1) / len(lots))
    if log:
        log(f"Đọc gộp xong: tách được {ok}/{total} câu" + (f" · {total - ok} câu đọc riêng" if ok < total else ""))
    return True


#: câu đọc lỗi hẳn (đọc lại vẫn hỏng) → đọc tạm bằng Edge thay vì để im lặng
FALLBACK = "Edge TTS"


def _fallback_config(cfg: TTSConfig, text: str) -> TTSConfig:
    """Cấu hình Edge dự phòng: giọng Edge đang chọn (câu tiếng Việt thì giọng vi-VN)."""
    voice = cfg.edge_voice or "vi-VN-HoaiMyNeural"
    if is_vietnamese(text) and not voice.lower().startswith("vi-"):
        voice = "vi-VN-HoaiMyNeural"
    return config_with_voice(cfg, "Edge TTS", voice)


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def generate_voice_track(cues: list[Cue], cfg: TTSConfig, settings: Settings, ffmpeg: FFmpeg, out_wav: Path, *,
                         on_progress: Callable[[float], None] | None = None,
                         log: Callable[[str], None] | None = None,
                         cancel: threading.Event | None = None,
                         fit_duration: float | None = None, speed: float = 1.0) -> VoiceTrackResult:
    """``speed``: tốc độ video (1.05 = 105 %). Track dựng thẳng theo mốc của video RA (mốc cue ÷ speed): mỗi câu chỉ
    time-stretch MỘT lần ở đây, render không còn atempo lần hai cho giọng. ``fit_duration``: thời lượng video ra
    (đã cắt, đã đổi tốc độ). Chế độ mềm tự nâng dần mức nén (1.10 → trần) tới khi track không dài quá video;
    trần tổng (tốc độ đọc × nén) = min(max_tempo, 1,25×) — câu vẫn tràn được báo trong ``overflow_cues``."""
    engine = make_engine(cfg, settings)
    voice_id = voice_id_for(cfg)
    speed = max(0.25, min(4.0, speed or 1.0))
    if abs(speed - 1.0) > 1e-3:
        cues = [Cue(c.index, c.start / speed, c.end / speed, c.text, dict(c.meta)) for c in cues]
    items = [(i, c, c.plain) for i, c in enumerate(cues) if c.plain]
    if not items:
        raise RuntimeError("Phụ đề trống — không có gì để đọc.")

    # giọng cho từng câu: nhiều giọng theo người nói (meta["speaker"]), còn lại giọng chính
    spk_cfgs = speaker_configs(cfg)
    engines: dict[str, TTSEngine] = {cfg.tts_provider: engine}
    plan: dict[int, tuple[TTSEngine, str, float, int]] = {}      # cue → (engine, giọng, tốc độ, cao độ)
    used: dict[str, int] = {}
    for i, c, _text in items:
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
    qc = qc_options(settings)
    polish = polisher(settings)

    def synth(i: int, text: str) -> Path:
        _check()                              # đã bấm Dừng → câu còn trong hàng chờ khỏi đọc
        eng, vid, sp, pt = plan[i]
        return synthesize_cached(eng, vid, text, speed=sp, pitch=pt, qc=qc, label=f"câu {i + 1}")

    def residual_of(i: int) -> float:
        eng, _vid, sp, _pt = plan[i]
        return eng.residual_tempo(sp)

    def _check() -> None:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ tạo giọng")

    # ── 1. Tổng hợp ───────────────────────────────────────────────────────
    paths: dict[int, Path] = {}
    workers = max(1, engine.workers)
    if log:
        log(f"Tổng hợp {len(items)} cue bằng {cfg.tts_provider} ({workers} luồng)…"
            + f" · kiểm chất lượng{' + Whisper nghe lại' if qc.asr else ''}")
        if len(used) > 1:
            log(f"Nhiều giọng: {len(used)} giọng — " + ", ".join(f"{k} ({n} câu)" for k, n in used.items()))
    base = 0.3 if prefill_batches([(*plan[i], text) for i, _c, text in items], settings, qc, log=log, check=_check,
                                  on_progress=on_progress) else 0.0
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(synth, i, text): i for i, _, text in items}
        failed: list[int] = []
        for fut in as_completed(futs):
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)     # huỷ câu chưa bắt đầu, không chờ đọc hết hàng
                raise RenderCancelled("Đã huỷ tạo giọng")
            i = futs[fut]
            try:
                paths[i] = fut.result()
            except Exception as exc:          # 1 câu lỗi (mạng/dịch vụ) → thử lại / engine dự phòng bên dưới, không hỏng cả job
                failed.append(i)
                logger.warning("TTS cue %d lỗi: %s", i + 1, exc)
            done += 1
            if on_progress:
                on_progress(base + (0.65 - base) * done / len(items))
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
    fallback_used: set[int] = set()
    if failed and cfg.tts_provider != FALLBACK:
        # vẫn lỗi → đọc tạm bằng Edge thay vì để câu đó im lặng
        fb_engine: TTSEngine | None = None
        for i in failed:
            _check()
            text = next(t for j, _c, t in items if j == i)
            try:
                fcfg = _fallback_config(cfg, text)
                fb_engine = fb_engine or make_engine(fcfg, settings)
                vid = voice_id_for(fcfg)
                paths[i] = synthesize_cached(fb_engine, vid, text, speed=plan[i][2], pitch=0, qc=qc, label=f"câu {i + 1}")
                plan[i] = (fb_engine, vid, plan[i][2], 0)
                fallback_used.add(i)
            except Exception as exc:
                logger.warning("TTS cue %d: Edge dự phòng cũng lỗi: %s", i + 1, exc)
        if fallback_used and log:
            rescued = sorted(fallback_used)
            log(f"⚠ {len(rescued)} câu lỗi với {cfg.tts_provider} → đọc tạm bằng {FALLBACK}: câu "
                + ", ".join(str(i + 1) for i in rescued[:10]) + ("…" if len(rescued) > 10 else ""))
        failed = [i for i in failed if i not in fallback_used]
    if failed:
        if len(failed) == len(items):
            raise RuntimeError("Không tổng hợp được câu nào — kiểm tra mạng / dịch vụ TTS.")
        if log:
            log(f"⚠ {len(failed)} câu không tổng hợp được (bỏ trống): {', '.join(str(i + 1) for i in failed[:8])}"
                + ("…" if len(failed) > 8 else ""))
        items = [it for it in items if it[0] not in failed]
    if log:
        bad = [i for i, _c, _t in items
               if (m := quality.read_mark(paths[i])) and int(m.get("score", 0)) >= quality.HARD]
        if qc.reread or bad:
            log(f"Kiểm chất lượng giọng: đã đọc lại {len(qc.reread)} câu"
                + (f" · {len(bad)} câu vẫn chưa đạt sau khi đọc lại — nên nghe lại: câu "
                   + ", ".join(str(i + 1) for i in bad[:10]) + ("…" if len(bad) > 10 else "") if bad else ""))

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
        return pos, polish(ffmpeg.decode_pcm(paths[order[pos]], sample_rate=SAMPLE_RATE, tempo=residual_of(order[pos])))

    base: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, fut in enumerate(as_completed([pool.submit(natural, pos) for pos in range(len(order))])):
            pos, pcm = fut.result()
            base[pos] = pcm
            if on_progress:
                on_progress(0.65 + 0.2 * (n + 1) / len(order))
    nat_dur = {pos: len(base[pos]) / _BYTES_PER_SEC for pos in base}

    total_cap = max(1.0, min(cfg.max_tempo, MAX_TOTAL_TEMPO))

    def fit_cap(pos: int) -> float:
        """Nén cho vừa khung tối đa của câu: cộng với tốc độ đọc không quá ``total_cap``
        (tốc độ đọc tự nó đã vượt trần thì không nén thêm)."""
        return max(1.0, total_cap / max(1e-6, residual_of(order[pos])))

    def tempo_for(pos: int, cap: float) -> float:
        slot = slot_for(pos)
        d = nat_dur[pos]
        if d > slot + 0.03:
            t = min(cap, fit_cap(pos), d / slot)
            return t if t > 1.02 else 1.0
        return 1.0

    def start_of(pos: int, cue: Cue, cursor: float) -> float:
        """Mốc bắt đầu đọc câu: đúng mốc phụ đề; cho vào sớm ≤ 0,2 s nếu câu trước đã xong (bớt khoảng «khựng» giữa
        hai câu), luôn cách câu trước tối thiểu ``min_gap``."""
        start = max(max(0.0, cue.start - PULL_EARLY), cursor)
        if pos > 0:
            start = max(start, cursor + cfg.min_gap)
        return start

    def plan_tempos(cap: float) -> tuple[dict[int, float], float]:
        """Mức nén từng câu + độ dài track khi áp mức tăng tốc tối đa ``cap`` (không cần giải mã lại). Khớp mềm: câu
        bắt đầu trễ hơn mốc phụ đề (câu trước tràn) đọc nhanh hơn để kết thúc đúng khung — đuổi kịp hình thay vì trễ
        dồn cả đoạn sau (như VideoLingo tăng tốc cả cụm câu), vẫn trong cùng trần nén."""
        tempos: dict[int, float] = {}
        cursor = 0.0
        for pos in range(len(order)):
            cue = by_index[order[pos]]
            t = tempo_for(pos, cap)
            start = start_of(pos, cue, cursor)
            if start - cue.start > CATCH_UP_LATE:
                room = cue.start + slot_for(pos) - start
                want = nat_dur[pos] / room if room > 0.1 else float("inf")
                t = max(t, min(cap, fit_cap(pos), want))
                t = t if t > 1.02 else 1.0
            tempos[pos] = t
            cursor = start + nat_dur[pos] / t
        return tempos, cursor

    def simulate(cap: float) -> float:
        """Độ dài track nếu áp mức tăng tốc tối đa ``cap``."""
        return plan_tempos(cap)[1]

    cap = 1.10
    if fit_duration and fit_duration > 0 and order:
        # nâng dần mức nén tới khi track không dài quá video (không vượt trần tổng của từng câu)
        top = max(fit_cap(p) for p in range(len(order)))
        for c in [round(1.10 + 0.05 * k, 2) for k in range(0, 40)]:
            if c > top + 0.05:
                break
            cap = c
            if simulate(c) <= fit_duration + 0.3:
                break
        if log and min(cap, top) > 1.10:
            log(f"Khớp mềm: nén tối đa ×{min(cap, top):.2f} để giọng không dài quá video "
                f"(tổng cùng tốc độ đọc ≤ ×{total_cap:.2f})")

    tempos = plan_tempos(cap)[0]

    def decode(pos: int) -> tuple[int, bytes, bool, bool, float]:
        _check()
        idx = order[pos]
        tempo = tempos[pos]
        pcm = base[pos]
        sped = False
        if tempo > 1.0:
            pcm = polish(ffmpeg.decode_pcm(paths[idx], sample_rate=SAMPLE_RATE, tempo=residual_of(idx) * tempo))
            sped = True
        dur = len(pcm) / _BYTES_PER_SEC
        overflow = dur > slot_for(pos) + 0.05
        return pos, pcm, sped, overflow, tempo

    decoded: dict[int, tuple[bytes, bool, bool, float]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs2 = [pool.submit(decode, pos) for pos in range(len(order))]
        for n, fut in enumerate(as_completed(futs2)):
            pos, pcm, sped, overflow, tempo = fut.result()
            decoded[pos] = (pcm, sped, overflow, tempo)
            if on_progress:
                on_progress(0.85 + 0.1 * (n + 1) / len(order))
    base.clear()

    # ── 3. Ghi WAV tuần tự ────────────────────────────────────────────────
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_wav.with_suffix(".part.wav")
    cursor = 0.0
    written = 0                        # số mẫu đã ghi — đệm lặng theo mẫu, không cộng dồn sai số làm tròn
    sped_up = overflow_n = 0
    synced: list[Cue] = []
    over: list[tuple[float, int]] = []
    report: list[dict] = []
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for pos in range(len(order)):
            _check()
            idx = order[pos]
            cue = by_index[idx]
            pcm, sped, overflow, tempo = decoded[pos]
            sped_up += int(sped)
            overflow_n += int(overflow)
            start = start_of(pos, cue, cursor)
            pad = int(round(start * SAMPLE_RATE)) - written
            if pad > 0:
                wf.writeframes(b"\x00\x00" * pad)
                written += pad
            wf.writeframes(pcm)
            written += len(pcm) // 2
            dur = len(pcm) / _BYTES_PER_SEC
            if overflow:
                over.append((dur - slot_for(pos), pos))
            synced.append(Cue(len(synced) + 1, start, start + max(0.3, dur), cue.text, dict(cue.meta)))
            cursor = start + dur
            eng, vid, _sp, _pt = plan[idx]
            mark = quality.read_mark(paths[idx]) or {}
            report.append({"cau": int(cue.meta.get("src_idx", idx)) + 1, "bat_dau": round(start, 3),
                           "ket_thuc": round(start + dur, 3), "khung": round(slot_for(pos), 3),
                           "tran": round(max(0.0, dur - slot_for(pos)), 3) if overflow else 0.0,
                           "engine": eng.name, "giong": vid, "tempo": round(residual_of(idx) * tempo, 3),
                           "so_lan_doc": int(mark.get("tries", 1)), "loi_qc": "; ".join(mark.get("problems") or []),
                           "du_phong": idx in fallback_used, "chu": cue.plain})
    tmp.replace(out_wav)
    if on_progress:
        on_progress(1.0)
    if log:
        log(f"Track giọng: {cursor:.1f}s · {sped_up} cue tăng tốc · {overflow_n} cue vẫn tràn · phụ đề dời theo giọng")
        if over:
            over.sort(reverse=True)
            log("Câu dài hơn khung phụ đề (nên rút gọn bản dịch): " + " · ".join(
                f"câu {int(by_index[order[pos]].meta.get('src_idx', order[pos])) + 1} "
                f"({_mmss(by_index[order[pos]].start)}) +{d:.1f}s «{by_index[order[pos]].plain[:32]}»"
                for d, pos in over[:6]) + (f" · và {len(over) - 6} câu khác" if len(over) > 6 else ""))
        if fit_duration and cursor > fit_duration + 0.5:
            log(f"⚠ Giọng dài hơn video {cursor - fit_duration:.1f}s dù đã nén tối đa (tổng ≤ ×{total_cap:.2f}) — "
                "video sẽ giữ khung cuối cho đủ giọng (không cắt). Nên rút gọn bản dịch.")
    overflow_cues = [(int(by_index[order[pos]].meta.get("src_idx", order[pos])), round(d, 3), round(slot_for(pos), 3))
                     for d, pos in sorted(over, key=lambda x: x[1])]
    return VoiceTrackResult(out_wav, len(items), sped_up, overflow_n, cursor, ensure_min_duration(synced, MIN_SUB),
                            overflow_cues, report)


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
