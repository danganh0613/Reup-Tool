"""Điều phối xử lý: probe → nhận diện → dịch → lồng tiếng → render, chạy nền.

- ``Pipeline`` chứa từng bước, dùng được riêng lẻ (nút «Tạo phụ đề cho video đang chọn»…).
- ``JobRunner`` chạy cả hàng đợi trong một thread.
- ``TaskRunner`` chạy một tác vụ lẻ trong thread riêng (mỗi lúc một tác vụ).
Mọi callback về UI đều đi qua ``ui_call`` (root.after) nên an toàn thread.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.config.paths import JOBS_DIR, PREVIEW_CACHE_DIR, SUBS_DIR, VOICE_DIR
from app.core.state import ENCODE_PRESETS, SRT_MODES, SRT_SOURCES, TRIM_MODES, TTS_SOURCES, AppState, VideoItem
from app.services import render_pipeline as rp
from app.services import stages as stages_mod
from app.services import word_timing
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, ensure_min_duration, load_srt, merge_bilingual, save_srt, shift, wrap_lines
from app.services.subtitle_style import write_ass

logger = logging.getLogger(__name__)

ProgressCb = Callable[[float], None]


class NoSpeech(RuntimeError):
    """Video không có lời nói (chỉ nhạc / không có luồng tiếng) — bỏ phụ đề + lồng tiếng, vẫn xuất được."""
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


def _same_source(scfg, tcfg) -> bool:
    """Chữ trên video cùng nguồn với chữ được đọc → phụ đề đi theo mốc của giọng đọc (song ngữ: dòng chính là bản
    dịch, nên đọc bản dịch cũng tính)."""
    return ((tcfg.tts_source == TTS_SOURCES[0] and scfg.srt_mode in (SRT_MODES[1], SRT_MODES[2]))
            or (tcfg.tts_source == TTS_SOURCES[1] and scfg.srt_mode == SRT_MODES[0]))


_LANG_NAMES = {"zh": "tiếng Trung", "yue": "tiếng Quảng Đông", "en": "tiếng Anh", "ja": "tiếng Nhật", "ko": "tiếng Hàn",
               "vi": "tiếng Việt", "th": "tiếng Thái", "id": "tiếng Indonesia", "fr": "tiếng Pháp", "de": "tiếng Đức",
               "es": "tiếng Tây Ban Nha", "ru": "tiếng Nga", "pt": "tiếng Bồ Đào Nha", "it": "tiếng Ý"}


#: bước người dùng thấy (theo thứ tự chạy) ← thông điệp pipeline ghi vào ``item.message``
STEPS: list[tuple[str, str]] = [
    ("read", "Đọc video"),
    ("stt", "Nhận diện lời thoại"),
    ("translate", "Dịch phụ đề"),
    ("speakers", "Nhận diện người nói"),
    ("voice", "Tạo giọng lồng"),
    ("novocal", "Khử giọng gốc"),
    ("render", "Xuất video"),
]
STEP_ORDER = [k for k, _ in STEPS]
_STEP_MESSAGES = [("Đọc thông tin", "read"), ("Nhận diện giọng nói", "stt"), ("Dịch phụ đề", "translate"),
                  ("Nhận diện người nói", "speakers"), ("Tạo giọng đọc", "voice"), ("Khử giọng gốc", "novocal"),
                  ("Ghép timeline", "render"), ("Render", "render")]
#: thời gian tương đối của từng bước (đo trên máy RTX 3060 Ti, video 1–3 phút) — chia thanh tiến độ cho sát thực tế
STEP_WEIGHTS = {"read": 0.2, "stt": 3.0, "translate": 2.0, "speakers": 1.0, "voice": 3.0, "novocal": 3.0, "render": 4.0}


def step_key(message: str) -> str | None:
    m = message or ""
    for prefix, key in _STEP_MESSAGES:
        if m.startswith(prefix):
            return key
    return None


def step_bands(steps: list[str]) -> dict[str, tuple[float, float]]:
    """Dải tiến độ (gốc, độ dài) của từng bước SẼ chạy, theo trọng số thời gian — chỉ còn bước xuất thì xuất chạy
    0 → 100 % (trước: luôn bắt đầu ở 65 %, «còn ~» báo sắp xong khi còn cả phút)."""
    total = sum(STEP_WEIGHTS.get(k, 1.0) for k in steps) or 1.0
    out, base = {}, 0.0
    for k in steps:
        span = STEP_WEIGHTS.get(k, 1.0) / total
        out[k] = (base, span)
        base += span
    return out


def _safe_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip() or "video"


#: Qwen nghe được ít hơn ngần này phần số chữ hiện trên hình → coi như video không có giọng đọc, lấy lời từ phụ đề in sẵn
SPEECH_MIN_SHARE = 0.25


class Pipeline:
    def __init__(self, state: AppState, ffmpeg: FFmpeg) -> None:
        self.state = state
        self.ffmpeg = ffmpeg
        self._srt_cache: dict[tuple[str, float], list[Cue]] = {}
        self.audio_only = False

    # ── Bước lẻ ───────────────────────────────────────────────────────────
    def probe(self, item: VideoItem) -> None:
        if not Path(item.path).exists():
            raise RuntimeError(f"Không tìm thấy file {item.name} — đã bị di chuyển / xoá, hoặc ổ ngoài chưa cắm. "
                               "Nạp lại video từ chỗ mới.")
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

    def file_stem(self, item: VideoItem) -> str:
        """Tên gốc cho file phụ đề / video xuất. Hàng đợi có video khác trùng tên (khác thư mục) → thêm mã đường dẫn,
        không ghi đè lên nhau."""
        stem = _safe_stem(item.stem)
        if any(v is not item and v.path != item.path and _safe_stem(v.stem).lower() == stem.lower()
               for v in self.state.videos):
            stem += "_" + hashlib.sha1(item.path.encode("utf-8")).hexdigest()[:6]
        return stem

    def _nospeech_known(self, item: VideoItem) -> bool:
        try:
            import json as _json

            data = _json.loads(self.nospeech_flag(item).read_text(encoding="utf-8"))
            return data.get("language") == self.state.subtitle.stt_language
        except (OSError, ValueError, AttributeError):
            return False

    def transcribe(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                   cancel: threading.Event | None = None, source_path: str | None = None,
                   force: bool = False) -> list[Cue]:
        """Lấy lời thoại thành phụ đề gốc — máy tự chọn cách, người dùng không phải chọn:
        - có Qwen3-ASR: nghe giọng nói; video có phụ đề in sẵn thì đọc chữ song song (CPU, lúc Qwen chạy GPU) rồi sửa
          chữ nghe sai theo chữ trên hình — câu + mốc của Qwen (đúng nhịp nói), chữ đúng như creator gõ (tên riêng);
        - chưa cài Qwen / video không có tiếng: chỉ đọc chữ in sẵn (mốc theo SenseVoice nếu có, AI gộp mẩu chữ thành câu).
        ``force``: nhận diện lại kể cả khi lần trước đã nghe là «không có lời» (nút ở tab Nhận diện)."""
        from app.services import ocr_hardsub, qwen_asr

        src = source_path or item.path
        own = source_path is None                      # «Nhận diện từ file khác»: chỉ nghe, không đọc chữ trên hình
        silent = self.silent(src)
        if silent or not qwen_asr.is_installed():
            cues = self.hardsub_cues(item, progress=progress, log=log, cancel=cancel) if own else None
            if not cues:
                if silent:
                    raise NoSpeech("Video không có tiếng và không có phụ đề in sẵn — không có lời thoại để lấy.")
                qwen_asr.require()                     # báo «chưa cài bộ nghe lời thoại» kèm nút Cài
            res = ocr_hardsub.finish(item, self.ffmpeg, cues, settings=self.state.settings, log=log, cancel=cancel)
            return self.save_transcript(item, res, log)
        if not force and own and self._nospeech_known(item):
            cues = self.hardsub_cues(item, progress=progress, log=log, cancel=cancel)
            if not cues:
                raise NoSpeech("Lần trước đã nghe: video không có lời nói.")
            res = ocr_hardsub.finish(item, self.ffmpeg, cues, settings=self.state.settings, log=log, cancel=cancel)
            return self.save_transcript(item, res, log)

        got: dict = {}
        parts = {"asr": 0.0, "ocr": 0.0}

        def part(key: str, x: float) -> None:
            parts[key] = x
            if progress:
                progress(0.7 * parts["asr"] + 0.3 * parts["ocr"] if own else parts["asr"])

        def read() -> None:
            try:
                got["cues"] = self.hardsub_cues(item, progress=lambda x: part("ocr", x), log=log, cancel=cancel)
            except BaseException as exc:               # noqa: BLE001 — Dừng: báo sau khi Qwen cũng dừng
                got["error"] = exc

        reader = threading.Thread(target=read, name="OCR", daemon=True) if own else None
        if reader:
            reader.start()
        try:
            res = qwen_asr.transcribe(src, self.ffmpeg, language=self.state.subtitle.stt_language,
                                      settings=self.state.settings, work_dir=self.job_dir(item),
                                      on_progress=lambda x: part("asr", x), log=log, cancel=cancel)
        except RenderCancelled:
            if reader:
                reader.join()
            raise
        except Exception as exc:
            if reader:
                reader.join()
            if not got.get("cues"):
                raise
            if log:                                    # nghe lỗi mà có chữ in sẵn → vẫn có phụ đề
                log(f"⚠ Qwen3-ASR lỗi ({error_line(exc)}) → dùng chữ in sẵn trên video.")
            res = ocr_hardsub.finish(item, self.ffmpeg, got["cues"], settings=self.state.settings, log=log,
                                     cancel=cancel)
            return self.save_transcript(item, res, log)
        if reader:
            reader.join()
        if isinstance(got.get("error"), RenderCancelled):
            raise got["error"]
        if got.get("cues"):
            res = self.with_hardsub(item, res, got["cues"], log=log, cancel=cancel)
        return self.save_transcript(item, res, log)

    def hardsub_cues(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                     cancel: threading.Event | None = None) -> list[Cue] | None:
        """Từng dòng phụ đề in sẵn trên video (đọc trên máy) — None: máy không đọc được chữ / video không có phụ đề in
        sẵn / đọc lỗi (lỗi chỉ mất phần sửa chữ, không hỏng bước nhận diện)."""
        from app.services import ocr_hardsub

        if not ocr_hardsub.available():
            return None
        try:
            cues = ocr_hardsub.read(item, self.ffmpeg, duration=self.ffmpeg.duration_of(item.path), on_progress=progress,
                                    log=log, cancel=cancel)
        except RenderCancelled:
            raise
        except ocr_hardsub.NoHardsub:
            return None
        except Exception as exc:                       # noqa: BLE001
            logger.warning("Đọc chữ in sẵn lỗi: %s", item.path, exc_info=True)
            if log:
                log(f"⚠ {item.name}: đọc chữ in sẵn lỗi ({error_line(exc)}) — chỉ dùng lời nghe được.")
            return None
        return cues or None

    def with_hardsub(self, item: VideoItem, res, ocr_cues: list[Cue], *, log: LogCb | None = None,
                     cancel: threading.Event | None = None):
        """Lời Qwen nghe được + phụ đề in sẵn của cùng video. Nghe được rất ít so với chữ trên hình (video không có giọng
        đọc — chỉ nhạc + phụ đề, kể chuyện bằng chữ…) → lấy lời từ chữ in sẵn; còn lại → sửa chữ nghe sai theo chữ trên
        hình. Phụ đề khác thứ tiếng với lời nói thì số chữ hai bên tương đương → vẫn theo lời nghe được (không sửa gì)."""
        from app.services.ocr_hardsub import finish, token_count

        heard = sum(token_count(c.plain) for c in res.cues)
        shown = sum(token_count(c.plain) for c in ocr_cues)
        if heard < SPEECH_MIN_SHARE * shown:
            if log:
                log("Video gần như không có lời nói — lấy lời từ chữ in sẵn trên video.")
            # Qwen đã nghe là không có lời → khỏi dò mốc tiếng nói (SenseVoice cũng không thấy gì), giữ mốc chữ trên hình
            return finish(item, self.ffmpeg, ocr_cues, speech_timing=False, settings=self.state.settings, log=log,
                          cancel=cancel)
        return self.fuse(res, ocr_cues, log)

    @staticmethod
    def fuse(res, ocr_cues: list[Cue], log: LogCb | None = None):
        """Lời Qwen nghe được + chữ in trên video → sửa chữ nghe sai (``ocr_hardsub.correct_with_ocr``)."""
        from app.services.ocr_hardsub import correct_with_ocr
        from app.services.whisper_stt import STTResult

        cues, fixes = correct_with_ocr(res.cues, ocr_cues)
        if log:
            if fixes:
                log(f"Sửa {len(fixes)} chỗ nghe sai theo chữ in trên video: "
                    + ", ".join(f"{a} → {b}" for a, b in fixes[:6]) + ("…" if len(fixes) > 6 else ""))
            else:
                log("Đối chiếu chữ in trên video: lời nghe được đã khớp.")
        return STTResult(cues=cues, language=res.language, language_probability=res.language_probability)

    def silent(self, src: str) -> bool:
        """Video không có luồng âm thanh (không đọc được thông tin thì coi như có — cứ thử nhận diện)."""
        try:
            return not self.ffmpeg.probe(src).has_audio
        except Exception:                               # noqa: BLE001
            return False

    def nospeech_flag(self, item: VideoItem) -> Path:
        return self.job_dir(item) / "nospeech.json"

    def save_transcript(self, item: VideoItem, res, log: LogCb | None = None) -> list[Cue]:
        """Lưu kết quả nhận diện thành phụ đề gốc của ``item`` (bản dịch / người nói cũ bỏ vì không còn khớp).
        SRT cũ (có thể đã sửa tay) giữ lại một bản ``.bak``."""
        if not res.cues:
            try:                                       # nhớ «không có lời» (theo ngôn ngữ đang chọn) → lần sau khỏi nghe lại
                flag = self.nospeech_flag(item)
                flag.parent.mkdir(parents=True, exist_ok=True)
                import json as _json

                flag.write_text(_json.dumps({"language": self.state.subtitle.stt_language}), encoding="utf-8")
            except OSError:
                pass
            raise NoSpeech("Không nghe thấy lời nói nào (video chỉ có nhạc?). Chọn đúng ngôn ngữ thay vì «Tự nhận "
                           "diện» nếu video có lời.")
        out = SUBS_DIR / f"{self.file_stem(item)}.srt"
        Path(str(out) + ".progress.json").unlink(missing_ok=True)
        try:                                       # nhớ video nguồn của SRT → nạp lại đúng video mới hỏi dùng lại
            out.with_suffix(".video.txt").write_text(item.path, encoding="utf-8")
        except OSError:
            pass
        if out.exists():
            try:
                import shutil as _shutil

                _shutil.copyfile(out, out.with_suffix(".srt.bak"))
            except OSError:
                pass
        save_srt(res.cues, out)
        item.srt_path = str(out)
        item.srt_translated_path = None
        try:
            self.speakers_path(item).unlink()          # người nói theo phụ đề cũ không còn khớp
        except OSError:
            pass
        if log:
            lang = _LANG_NAMES.get(res.language, res.language or "chưa rõ")
            log(f"Nhận diện xong: {len(res.cues)} câu · nhận ra {lang} → {out.name}")
        return res.cues

    def translate(self, item: VideoItem, cues: list[Cue] | None = None, *, progress: ProgressCb | None = None,
                  log: LogCb | None = None, cancel: threading.Event | None = None) -> list[Cue]:
        """Dịch 2 bước như VideoLingo (tóm tắt + thuật ngữ → dịch sát → tự phê bình + dịch thoát) + khớp thời lượng
        lồng tiếng theo nhịp đọc thật (nếu bật lồng tiếng)."""
        from app.services.translate_vl import translate_cues_vl

        cfg = self.state.subtitle
        if cues is None:
            if not item.srt_path:
                raise RuntimeError("Chưa có phụ đề gốc để dịch.")
            cues = load_srt(item.srt_path)
        from app.services.translate_vl import same_language

        if same_language([c.plain for c in cues], cfg.translate_target, cfg.source_hint):
            out = self.translated_path(item)
            save_srt(cues, out)
            item.srt_translated_path = str(out)
            if log:
                log(f"{item.name}: lời thoại đã là {cfg.translate_target} — bỏ qua dịch, dùng luôn lời gốc.")
            return cues
        t = self.state.tts
        fit = ({"tts_speed": t.tts_speed, "video_speed": self.state.video.speed / 100, "min_gap": t.min_gap}
               if t.enable_tts else None)
        out_cues = translate_cues_vl(cues, settings=self.state.settings, target=cfg.translate_target,
                                     source_hint=cfg.source_hint, style=cfg.translate_style,
                                     srt_path=item.srt_path, fit=fit, on_progress=progress, log=log, cancel=cancel)
        out = self.translated_path(item)
        save_srt(out_cues, out)
        item.srt_translated_path = str(out)
        if log:
            log(f"Dịch xong → {out.name}")
        return out_cues

    def attach_srt(self, item: VideoItem, path: str) -> None:
        """Gắn file phụ đề gốc khác cho video (kéo thả / «Chọn SRT»): bản dịch, người nói, giọng có sẵn làm theo SRT cũ
        không còn khớp → bỏ (trước: kéo thả chỉ đổi SRT gốc, Chuẩn bị vẫn dùng bản dịch cũ)."""
        item.srt_path = str(path)
        item.srt_translated_path = None
        item.voice_locked = False
        try:
            self.speakers_path(item).unlink()
        except OSError:
            pass

    def translated_path(self, item: VideoItem) -> Path:
        from app.services.translate_router import LANG_CODE

        cfg = self.state.subtitle
        tag = LANG_CODE.get(cfg.translate_target) or _safe_stem(cfg.translate_target.split(" ")[-1]).lower()[:8] or "dich"
        return SUBS_DIR / f"{self.file_stem(item)}.{tag}.srt"

    def retranslate(self, item: VideoItem, idxs: list[int] | None = None, *, instruction: str = "",
                    previous: list[str] | None = None, save: bool = True, progress: ProgressCb | None = None,
                    log: LogCb | None = None, cancel: threading.Event | None = None,
                    budgets: dict[int, int] | None = None) -> dict[int, str]:
        """AI dịch lại (cả video hoặc các câu ``idxs``) theo ``instruction``, xem bản dịch hiện tại làm «bản bị chê».
        ``save``: ghi luôn vào SRT đã dịch (giọng lồng tự tạo lại vì SRT đổi); False: chỉ trả kết quả để người dùng
        xem trước rồi tự lưu. ``budgets``: giới hạn ký tự riêng cho từng câu (rút gọn câu tràn khung)."""
        from app.services.translate_router import retranslate

        cfg = self.state.subtitle
        if not item.srt_path or not Path(item.srt_path).exists():
            raise RuntimeError("Chưa có phụ đề gốc để dịch lại.")
        orig = load_srt(item.srt_path)
        tr_path = item.srt_translated_path if item.srt_translated_path and Path(item.srt_translated_path).exists() else None
        current = load_srt(tr_path) if tr_path else []
        idxs = list(range(len(orig))) if idxs is None else idxs
        if len(current) != len(orig):
            if save and current and set(idxs) != set(range(len(orig))):
                # bản dịch đã gộp / xoá câu: chỉ số câu không khớp bản gốc — ghi từng câu sẽ đè mất chữ đã sửa tay
                raise RuntimeError("Bản dịch đã gộp / xoá câu nên số câu khác bản gốc — không dịch lại từng câu được. "
                                   "Dịch lại cả video, hoặc sửa tay ở tab Lời thoại.")
            current = []                              # bản dịch lệch số câu → dịch lại cả video từ gốc
        if previous is None:
            previous = [c.plain for c in current]
        from app.services.translate_vl import memory_style

        style = "" if instruction.strip() == cfg.translate_style.strip() else cfg.translate_style   # khỏi lặp 2 lần
        new = retranslate(orig, previous, idxs, settings=self.state.settings, target=cfg.translate_target,
                          source_hint=cfg.source_hint, style=style, instruction=instruction,
                          on_progress=progress, log=log, cancel=cancel, budgets=budgets,
                          memory_key=memory_style(cfg.translate_style, self.state.settings.glossary,
                                                 self.state.settings.active_model()))
        if save and new:
            base = current or orig
            out = [Cue(c.index, c.start, c.end, wrap_lines(new[i]) if i in new else c.text, dict(c.meta))
                   for i, c in enumerate(base)]
            dst = Path(tr_path) if tr_path else self.translated_path(item)
            save_srt(out, dst)
            item.srt_translated_path = str(dst)
            if log:
                log(f"Dịch lại xong {len(new)} câu → {dst.name}")
        return new

    # ── Người nói (lồng tiếng nhiều giọng) ────────────────────────────────
    def speakers_path(self, item: VideoItem) -> Path:
        h = hashlib.sha1(item.path.encode("utf-8")).hexdigest()[:8]
        return SUBS_DIR / f"{_safe_stem(item.stem)[:60]}_{h}.speakers.json"

    # ── Câu «không đọc» (giữ âm gốc: lời bài hát, câu trích…) ─────────────
    def noread_path(self, item: VideoItem) -> Path:
        h = hashlib.sha1(item.path.encode("utf-8")).hexdigest()[:8]
        return SUBS_DIR / f"{_safe_stem(item.stem)[:60]}_{h}.noread.json"

    def load_noread(self, item: VideoItem) -> list[float]:
        """Mốc bắt đầu (giây, theo video gốc) của các câu người dùng đánh dấu «không đọc» — khớp theo mốc nên vẫn đúng
        cho cả lời gốc lẫn bản dịch; nhận diện lại (mốc đổi) thì tự hết hiệu lực."""
        try:
            import json as _json

            data = _json.loads(self.noread_path(item).read_text(encoding="utf-8"))
            return sorted(float(x) for x in data.get("starts") or [])
        except (OSError, ValueError, TypeError, AttributeError):
            return []

    def save_noread(self, item: VideoItem, starts) -> None:
        import json as _json

        path = self.noread_path(item)
        starts = sorted({round(float(x), 3) for x in starts})
        if not starts:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"video": item.path, "starts": starts}, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def is_noread(cue: Cue, starts: list[float]) -> bool:
        return any(abs(cue.start - s) <= 0.05 for s in starts)

    def mark_noread(self, item: VideoItem, cues: list[Cue]) -> list[tuple[float, float]]:
        """Gắn ``meta["noread"]`` cho câu không đọc; trả các khoảng (đầu, cuối) theo mốc nguồn — để giữ âm gốc ở đó."""
        starts = self.load_noread(item)
        spans = []
        for c in cues:
            if starts and self.is_noread(c, starts):
                c.meta["noread"] = True
                spans.append((c.start, c.end))
        return spans

    def speaker_map(self, item: VideoItem):
        from app.services.diarize import SpeakerMap

        return SpeakerMap.load(self.speakers_path(item))

    def annotate_speakers(self, item: VideoItem, cues: list[Cue]) -> None:
        """Ghi người nói vào ``meta`` của cue (mốc thời gian video gốc) khi bật «Nhiều giọng»."""
        if not self.state.tts.multi_voice or not cues:
            return
        smap = self.speaker_map(item)
        if smap is not None:
            smap.annotate(cues)

    def ensure_speakers(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                        cancel: threading.Event | None = None) -> None:
        """Bật «Nhiều giọng» mà video chưa nhận diện người nói → nhận diện luôn (lỗi thì lồng tiếng một giọng)."""
        if not self.state.tts.multi_voice or self.speakers_path(item).exists():
            return
        try:
            self.diarize(item, progress=progress, log=log, cancel=cancel)
        except RenderCancelled:
            raise
        except Exception as exc:
            if log:
                log(f"⚠ Không nhận diện được người nói ({exc}) — lồng tiếng một giọng.")

    def diarize(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                cancel: threading.Event | None = None):
        """Nhận diện ai nói câu nào (theo phụ đề gốc) → lưu ``speakers.json`` cạnh phụ đề."""
        from app.services.diarize import diarize

        if not item.srt_path or not Path(item.srt_path).exists():
            raise RuntimeError("Chưa có phụ đề gốc — bấm «Tạo phụ đề cho video đang chọn» ở tab Nhận diện hoặc chọn file SRT trước.")
        sc = self.state.tts.speaker_count
        n = int(sc) if sc.isdigit() else 0
        smap = diarize(item.path, load_srt(item.srt_path), self.ffmpeg, n_speakers=n, on_progress=progress, log=log,
                       cancel=cancel)
        smap.save(self.speakers_path(item))
        return smap

    def voice_signature(self, item: VideoItem, srt_path: str, extra_key: str = "") -> str:
        from app.services.tts.generator import post_signature

        t = self.state.tts
        try:
            mtime = int(Path(srt_path).stat().st_mtime)
        except OSError:
            mtime = 0
        try:                                       # câu «không đọc» đổi → giọng đổi
            noread = int(self.noread_path(item).stat().st_mtime_ns)
        except OSError:
            noread = 0
        multi = ""
        if t.multi_voice:
            try:
                multi = f"{t.speaker_voices!r}{int(self.speakers_path(item).stat().st_mtime_ns)}"
            except OSError:
                multi = repr(t.speaker_voices)
        raw = "|".join(str(x) for x in (
            "v2", t.tts_provider, t.edge_voice, t.vieneu_voice, t.tts_speed, t.tts_pitch, t.max_tempo, t.min_gap,
            srt_path, mtime, extra_key, post_signature(self.state.settings),
            self.state.video.speed)                # track dựng theo mốc video ra → phụ thuộc tốc độ video
            + ((multi,) if multi else ()) + ((f"noread{noread}",) if noread else ()))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def srt_sha(path: str | None) -> str:
        """Băm nội dung SRT — chữ ký track chỉ có mốc sửa (làm tròn giây): sửa 2 lần cùng giây vẫn nhận ra."""
        try:
            return hashlib.sha1(Path(path or "").read_bytes()).hexdigest()[:12]
        except OSError:
            return ""

    @classmethod
    def track_matches(cls, track: Path, srt_path: str | None) -> bool:
        """Track (đúng chữ ký) còn khớp nội dung SRT không — track cũ chưa ghi băm thì coi như khớp."""
        try:
            import json as _json

            want = _json.loads(track.with_suffix(".json").read_text(encoding="utf-8")).get("srt_sha")
        except (OSError, ValueError, AttributeError):
            return True
        return not want or not srt_path or want == cls.srt_sha(srt_path)

    def generate_voice(self, item: VideoItem, cues: list[Cue] | None = None, *, srt_path: str | None = None,
                       progress: ProgressCb | None = None, log: LogCb | None = None,
                       cancel: threading.Event | None = None, force: bool = False, extra_key: str = "",
                       fit_duration: float | None = None, out_dir: Path | None = None) -> Path:
        from app.services.tts.generator import generate_voice_track, voice_id_for

        if cues is None:
            srt_path = srt_path or self._tts_srt_path(item)
            cues = load_srt(srt_path)
            self.ensure_speakers(item, log=log, cancel=cancel)
            self.annotate_speakers(item, cues)
            self.mark_noread(item, cues)
        srt_path = srt_path or item.srt_path or ""
        out = (out_dir or VOICE_DIR) / f"{_safe_stem(item.stem)}_{self.voice_signature(item, srt_path, extra_key)}.wav"
        if out.exists() and not force and self.track_matches(out, srt_path):
            if log:
                log(f"Dùng lại track giọng đã có: {out.name}")
            item.voice_path = str(out)
            return out
        # track dựng thẳng theo mốc video ra (đã đổi tốc độ): mỗi câu chỉ time-stretch một lần
        vspeed = max(0.25, min(4.0, self.state.video.speed / 100.0))
        res = generate_voice_track(cues, self.voice_config(item), self.state.settings, self.ffmpeg, out,
                                   on_progress=progress, log=log, cancel=cancel, speed=vspeed,
                                   fit_duration=fit_duration / vspeed if fit_duration else None)
        synced = out.with_suffix(".srt")
        if res.synced_cues:
            save_srt(res.synced_cues, synced)      # phụ đề đã dời theo vị trí thật của giọng
        elif synced.exists():
            synced.unlink()
        # ghi chú nguồn để "Thư viện voice" biết track này tạo từ SRT nào + dựng theo tốc độ video nào
        try:
            import json as _json
            out.with_suffix(".json").write_text(_json.dumps({
                "video": item.path, "srt": srt_path, "provider": self.state.tts.tts_provider,
                "voice": voice_id_for(self.state.tts), "speed": self.state.tts.tts_speed,
                "duration": res.duration, "cues": res.cue_count, "created": time.time(),
                "synced_srt": str(synced) if res.synced_cues else "", "timeline_speed": vspeed,
                "overflow_cues": res.overflow_cues, "failed": res.failed, "fallback": res.fallback,
                "qc_bad": res.qc_bad, "noread": res.noread, "srt_sha": self.srt_sha(srt_path)},
                ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            logger.exception("Không ghi được ghi chú track giọng")
        if out_dir is None:
            self._prune_tracks(item, keep=out)
        if res.report:                             # báo cáo từng câu: engine, tempo, số lần đọc, lỗi kiểm tra…
            try:
                import csv

                with open(out.with_suffix(".cues.csv"), "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.DictWriter(f, fieldnames=list(res.report[0].keys()))
                    w.writeheader()
                    w.writerows(res.report)
            except OSError:
                logger.exception("Không ghi được báo cáo từng câu")
        item.voice_path = str(out)
        return out

    TRACKS_KEPT = 3            # mỗi video giữ 3 track gần nhất (đổi qua lại giọng / Cắt không phải đọc lại)

    def _prune_tracks(self, item: VideoItem, keep: Path) -> None:
        """Mỗi lần sửa lời / đổi giọng để lại một track WAV mới (~5 MB / 2 phút video) → xoá bớt track cũ của CHÍNH
        video này (ghi chú .json có đường dẫn video), giữ vài bản gần nhất + bản đang dùng / đang chọn."""
        import json as _json

        mine = []
        for meta in VOICE_DIR.glob(f"{_safe_stem(item.stem)}_*.json"):
            wav = meta.with_suffix(".wav")
            if wav == keep or not wav.exists():
                continue
            try:
                if _json.loads(meta.read_text(encoding="utf-8")).get("video") == item.path:
                    mine.append(wav)
            except (OSError, ValueError, AttributeError):
                continue
        mine.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        protect = {str(Path(p)) for p in (item.voice_path,) if p}
        for wav in mine[self.TRACKS_KEPT - 1:]:
            # track vừa tạo (< 30 phút: các đoạn của chế độ «mỗi đoạn một file» cùng lượt) / đang dùng → giữ
            if str(wav) in protect or time.time() - wav.stat().st_mtime < 1800:
                continue
            for ext in (".wav", ".srt", ".json", ".cues.csv", ".words.json"):
                try:
                    wav.with_suffix(ext).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def voice_overflow(voice: Path | None, *, untried: bool = False) -> list[tuple[int, float, float]]:
        """Các câu tràn khung của track (chỉ số cue gốc, giây tràn, khung) — ghi trong .json đi kèm.
        ``untried``: bỏ các câu đã nhờ AI rút gọn (mỗi câu một lần, không gọi lại mỗi lần render)."""
        if not voice:
            return []
        try:
            import json as _json

            data = _json.loads(Path(voice).with_suffix(".json").read_text(encoding="utf-8"))
            tried = {int(i) for i in data.get("shortened") or []} if untried else set()
            return [(int(a), float(b), float(c)) for a, b, c in data.get("overflow_cues") or [] if int(a) not in tried]
        except (OSError, ValueError, TypeError):
            return []

    @staticmethod
    def mark_shortened(voice: Path | None, idxs) -> None:
        """Ghi vào .json của track các câu đã nhờ AI rút gọn → lần render sau không gọi lại."""
        if not voice:
            return
        try:
            import json as _json

            meta = Path(voice).with_suffix(".json")
            data = _json.loads(meta.read_text(encoding="utf-8"))
            data["shortened"] = sorted({int(i) for i in data.get("shortened") or []} | {int(i) for i in idxs})
            meta.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError, TypeError):
            logger.exception("Không ghi được ghi chú rút gọn")

    @staticmethod
    def line_key(text: str) -> str:
        return hashlib.sha1(" ".join((text or "").split()).encode("utf-8")).hexdigest()[:10]

    @staticmethod
    def line_locks(tr_path: str | None) -> dict[str, set[str]]:
        """{"shortened": câu gốc đã nhờ AI rút gọn, "edited": câu người dùng tự sửa bản dịch} — đừng rút gọn lại."""
        try:
            import json as _json

            data = _json.loads(Path(str(tr_path) + ".locks.json").read_text(encoding="utf-8"))
            return {k: set(data.get(k) or []) for k in ("shortened", "edited")}
        except (OSError, ValueError, TypeError, AttributeError):
            return {"shortened": set(), "edited": set()}

    @classmethod
    def add_line_locks(cls, tr_path: str | None, kind: str, texts) -> None:
        if not tr_path:
            return
        import json as _json

        locks = cls.line_locks(tr_path)
        locks[kind] |= {cls.line_key(t) for t in texts if t}
        try:
            Path(str(tr_path) + ".locks.json").write_text(
                _json.dumps({k: sorted(v) for k, v in locks.items()}), encoding="utf-8")
        except OSError:
            logger.warning("Không ghi được danh sách câu đã rút gọn / sửa tay", exc_info=True)

    def voice_config(self, item: VideoItem):
        """Cấu hình giọng cho video này: nhiều giọng → giọng đã chọn / của mẫu gán đúng người nói (theo giới tính)."""
        t = self.state.tts
        if not t.multi_voice:
            return t
        from app.services.diarize import resolve_voices, suggest_voices
        from app.services.tts.generator import main_locale, make_engine, voice_id_for

        smap = self.speaker_map(item)
        voices = resolve_voices(t.speaker_voices, smap)
        if smap is not None and len(voices) < len(smap.speakers):
            # người nói chưa có giọng (chưa mở bảng người nói / mẫu thiếu giới tính đó) → tự gợi ý giọng riêng theo
            # Nam / Nữ, khác nhau — không để ai cũng dùng chung giọng chính
            try:
                pool = make_engine(t, self.state.settings).list_voices()
                voices = suggest_voices([smap.speakers[n] for n in smap.names()], pool, provider=t.tts_provider,
                                        locale=main_locale(t), main_voice=voice_id_for(t), existing=voices)
            except Exception:
                logger.debug("Không gợi ý được giọng người nói", exc_info=True)
        return dataclasses.replace(t, speaker_voices=voices)

    def _tts_srt_path(self, item: VideoItem) -> str:
        want_translated = self.state.tts.tts_source == TTS_SOURCES[0]
        if want_translated and item.srt_translated_path:
            return item.srt_translated_path
        if item.srt_path:
            return item.srt_path
        raise RuntimeError("Chưa có phụ đề để tạo giọng (nhận diện hoặc chọn file SRT trước).")

    # ── Xem trước ─────────────────────────────────────────────────────────
    def current_voice_track(self, item: VideoItem, duration: float = 0.0) -> tuple[Path | None, bool, bool]:
        """Track giọng để nghe kèm khi phát xem trước: (đường dẫn, khớp cấu hình hiện tại?, tính theo thời gian sau cắt?).

        Ưu tiên track đúng chữ ký cấu hình hiện tại (cùng file bản render sẽ dùng lại); không có thì track tạo gần nhất."""
        if not self.state.tts.enable_tts:
            return None, False, False
        try:
            srt = self._tts_srt_path(item)
        except RuntimeError:
            return None, False, False
        trim = stages_mod.trim_spec(self.state.trim, duration or item.duration or 0.0)
        key = repr([(round(a, 3), round(b, 3)) for a, b in trim.segments]) if trim.active else ""
        p = VOICE_DIR / f"{_safe_stem(item.stem)}_{self.voice_signature(item, srt, key)}.wav"
        if p.exists():
            return p, self.track_matches(p, srt), trim.active
        if item.voice_path and Path(item.voice_path).exists():
            return Path(item.voice_path), False, False
        return None, False, False

    def preview_sub_cues(self, item: VideoItem, voice: Path | None, fresh: bool = True) -> list[Cue]:
        """Phụ đề sẽ ghi lên video (theo thời gian nguồn) — dùng mốc đã dời theo giọng nếu bản render cũng dùng.
        ``fresh=False`` (giọng là bản cũ, vd vừa sửa SRT): mốc của giọng cũ nhưng CHỮ lấy từ file hiện tại."""
        scfg, tcfg = self.state.subtitle, self.state.tts
        synced = voice.with_suffix(".srt") if voice else None
        same_source = tcfg.enable_tts and _same_source(scfg, tcfg)
        if synced is not None and synced.exists() and same_source and not self.state.trim.enable:
            try:
                cues = load_srt(synced)
                if not fresh:
                    cur = load_srt(self._tts_srt_path(item))
                    if len(cur) != len(cues):
                        return self.subtitle_cues(item)
                    for c, n in zip(cues, cur):
                        c.text = n.text
                if scfg.srt_mode == SRT_MODES[2]:          # song ngữ: thêm dòng lời gốc của cùng câu
                    orig = load_srt(item.srt_path) if item.srt_path and Path(item.srt_path).exists() else []
                    if len(orig) != len(cues):
                        return self.subtitle_cues(item)
                    cues = merge_bilingual(cues, orig)
                if scfg.karaoke:                 # mốc chữ đã căn (không chạy model ở đây — tính ở bước chuẩn bị)
                    cues = word_timing.attach(cues, voice, self.ffmpeg)
                # track mới dựng theo mốc video ra → nhân lại tốc độ lúc dựng để về mốc nguồn
                return rp.scale_cues(shift(cues, scfg.sub_delay), rp.voice_timeline_speed(voice))
            except Exception:
                pass
        return self.subtitle_cues(item)

    def preview_clip(self, item: VideoItem, start: float, seconds: float = 15.0, *, progress: ProgressCb | None = None,
                     log: LogCb | None = None, cancel: threading.Event | None = None) -> tuple[Path, list[tuple[float, float]]]:
        """Render thử ``seconds`` giây (sau cắt) từ mốc nguồn ``start`` bằng đúng quy trình render → MP4 trong cache.

        Không đụng tới trạng thái hàng đợi; phụ đề nhận diện / bản dịch tạo ra trong lúc này vẫn được giữ cho video."""
        log = log or (lambda _m: None)
        info = self.ffmpeg.probe(item.path)
        if info.width <= 0 or info.height <= 0:
            raise RuntimeError("File không có luồng hình.")
        self.ensure_hardsub(item, info, log, cancel)
        trim = stages_mod.collect(self.state, item, info).trim
        segs = trim.segments if trim.active else [(0.0, info.duration)]
        speed = max(0.25, self.state.video.speed / 100.0)
        need = seconds * speed                      # giây nguồn cho ``seconds`` giây video ra
        window: list[tuple[float, float]] = []
        for a, b in segs:
            if b <= start + 0.05:
                continue
            a2 = max(a, start)
            take = min(b - a2, need)
            if take > 0.05:
                window.append((a2, a2 + take))
                need -= take
            if need <= 0.05:
                break
        if not window:
            raise RuntimeError("Vị trí đang xem nằm sau đoạn cuối được giữ lại — tua về trước rồi thử lại.")
        clip = dataclasses.replace(item)
        out = PREVIEW_CACHE_DIR / "render_preview.mp4"
        PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._render_one(clip, info, rp.TrimSpec(segments=window), on_update=lambda _it: progress(_it.progress) if progress else None,
                         log=log, cancel=cancel, final=False, preview_out=out)
        if clip.srt_path != item.srt_path or clip.srt_translated_path != item.srt_translated_path:
            item.srt_path, item.srt_translated_path = clip.srt_path, clip.srt_translated_path
        try:                                    # giọng của các lần xem thử: giữ 12 file gần nhất
            old = sorted((PREVIEW_CACHE_DIR / "voice").glob("*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in old[12:]:
                for ext in (".wav", ".srt", ".json", ".cues.csv", ".words.json"):
                    f.with_suffix(ext).unlink(missing_ok=True)
        except OSError:
            pass
        return out, window

    def ensure_hardsub(self, item: VideoItem, info, log: LogCb | None = None,
                       cancel: threading.Event | None = None) -> None:
        """«Tự dò chữ cứng» bật → dò vùng phụ đề in sẵn của video (một lần, có cache) trước khi dựng filter."""
        if self.state.blur.auto_detect:
            from app.services.hardsub import ensure_region

            ensure_region(item, self.ffmpeg, info.duration, log=log, cancel=cancel)

    def output_path(self, item: VideoItem, suffix: str = "", ext: str = ".mp4", create: bool = True) -> Path:
        """Đường dẫn file xuất theo «Tên file xuất» / «Thư mục con» của cấu hình (mã {ten} {hauto} {mau} {ngay} {gio} {stt})."""
        st, vcfg = self.state, self.state.video
        tpl = item.template or st.template_name or ""
        tpl = "".join(c for c in tpl if c.isalnum() or c in " -_.()").strip() or "mau"
        try:
            stt = st.videos.index(item) + 1
        except ValueError:
            stt = 1
        now = time.localtime()
        stem = self.file_stem(item)
        stem = item.stem if stem == _safe_stem(item.stem) else stem      # trùng tên video khác → kèm mã đường dẫn
        tokens = {"ten": stem, "hauto": vcfg.output_suffix, "mau": tpl, "ngay": time.strftime("%Y-%m-%d", now),
                  "gio": time.strftime("%H%M", now), "stt": f"{stt:02d}"}

        def fill(pattern: str) -> str:
            out = pattern
            for k, v in tokens.items():
                out = out.replace("{" + k + "}", v)
            return out

        name = fill(vcfg.output_name.strip()) if vcfg.output_name.strip() else f"{stem}{vcfg.output_suffix}"
        name = "".join(c if c not in '\\/:*?"<>|' else "_" for c in name).strip() or stem
        out_dir = Path(st.output_dir) if st.output_dir else VOICE_DIR.parent / "videos"
        sub = fill(vcfg.output_subdir.strip()).replace("\\", "/")
        for part in [p for p in sub.split("/") if p.strip() and p.strip() not in (".", "..")]:
            out_dir = out_dir / "".join(c if c not in ':*?"<>|' else "_" for c in part).strip()
        if create:
            out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{name}{suffix}{ext}"

    def planned_steps(self, item: VideoItem, mode: str) -> list[str]:
        """Các bước video này sẽ đi qua (tính lúc bắt đầu, theo cấu hình của pipeline). Bước đã có sẵn kết quả
        (SRT, bản dịch, người nói, giọng khớp cấu hình, bản khử giọng) được bỏ — chỉ còn việc thật sự phải chờ."""
        st = self.state
        s, t, v = st.subtitle, st.tts, st.video
        has = lambda p: bool(p) and Path(p).exists()      # noqa: E731
        steps = ["read"]
        need_cues = s.enable_subtitle or t.enable_tts
        want_tr = (s.enable_subtitle and s.srt_mode != SRT_MODES[0]) or (t.enable_tts and t.tts_source == TTS_SOURCES[0])
        no_srt = need_cues and not has(item.srt_path) and s.srt_source == SRT_SOURCES[0]
        if no_srt:
            steps.append("stt")
        no_tr = need_cues and want_tr and s.translate_enable and not has(item.srt_translated_path)
        if no_tr:
            steps.append("translate")
        locked = item.voice_locked and has(item.voice_path)
        if t.enable_tts and t.multi_voice and not locked:
            try:
                done = self.speakers_path(item).exists()
            except Exception:                           # noqa: BLE001
                done = False
            if not done:
                steps.append("speakers")
        if t.enable_tts and not locked:
            try:                                        # phụ đề có sẵn + giọng khớp cấu hình → không đọc lại
                fresh = not (no_srt or no_tr) and self.current_voice_track(item)[1]
            except Exception:                           # noqa: BLE001
                fresh = False
            if not fresh:
                steps.append("voice")
        if v.demucs_enable and v.keep_original_audio and not v.mute and item.has_audio:
            from app.services.vocal_remove import cached_no_vocals

            if cached_no_vocals(item.path) is None:
                steps.append("novocal")
        if mode == "render":
            steps.append("render")
        return steps

    def _free_output(self, item: VideoItem, out: Path) -> Path:
        """File xuất đã là file xuất của video KHÁC (mẫu tên không có {ten} / {stt}, hai video cùng tên) → thêm _2,
        _3… thay vì ghi đè. Xuất lại chính video này thì ghi đè như cũ."""
        taken = {str(v.output_path).lower() for v in self.state.videos if v is not item and v.output_path}
        if str(out).lower() not in taken:
            return out
        k = 2
        while True:
            cand = out.with_name(f"{out.stem}_{k}{out.suffix}")
            if str(cand).lower() not in taken and not cand.exists():
                return cand
            k += 1

    @staticmethod
    def _stager(item: VideoItem, on_update, bands: dict[str, tuple[float, float]] | None = None):
        """``stage(tên, gốc, độ dài)`` → callback tiến độ ghi vào ``item`` (gộp cập nhật ~7 lần/giây).
        ``bands``: dải theo bước thật của lượt chạy (``step_bands``) — thay cho gốc / độ dài cố định; bước không có
        trong kế hoạch giữ nguyên tiến độ. Thanh không bao giờ đi lùi trong một lượt."""
        def stage(name: str, base: float, span: float) -> ProgressCb:
            last = [0.0]
            if bands is not None:
                key = step_key(name)
                base, span = bands.get(key, (item.progress, 0.0)) if key else (item.progress, 0.0)

            def cb(p: float) -> None:
                now = time.time()
                if now - last[0] < 0.15 and p < 1.0:
                    return
                last[0] = now
                val = base + span * max(0.0, min(1.0, p))
                item.progress = max(item.progress, val) if bands is not None else val
                item.message = name
                on_update(item)
            return cb
        return stage

    def prepare(self, item: VideoItem, *, on_update: Callable[[VideoItem], None], log: LogCb,
                cancel: threading.Event, bands: dict | None = None) -> None:
        """Bước «Chuẩn bị» (chưa render): nhận diện giọng, dịch, người nói, tạo giọng lồng → «Chờ duyệt».
        ``bands``: dải tiến độ JobRunner tính lúc bắt đầu lượt (đã có phần nghe lời thoại chạy trước)."""
        if bands is None:
            bands, item.progress = step_bands(self.planned_steps(item, "prepare")), 0.0
        item.status, item.message = "Đang chuẩn bị", "Đọc thông tin"
        on_update(item)
        self.probe(item)
        info = self.ffmpeg.probe(item.path)
        if info.width <= 0 or info.height <= 0:
            raise RuntimeError("File không có luồng hình (audio thuần) — chỉ xử lý được video.")
        self.ensure_hardsub(item, info, log, cancel)
        trim = stages_mod.collect(self.state, item, info).trim
        # «Xuất mỗi đoạn thành 1 file»: giọng tạo theo từng đoạn — giống hệt lúc render để dùng lại được
        trims = [rp.TrimSpec(segments=[seg]) for seg in trim.segments] \
            if trim.active and self.state.trim.mode == TRIM_MODES[1] and len(trim.segments) > 1 else [trim]
        stage = self._stager(item, on_update, bands)
        for t in trims:
            self._prepare_assets(item, info, t, vcfg=self.state.video, stage=stage, log=log, cancel=cancel,
                                 on_update=on_update)
        item.status, item.progress, item.message = "Chờ duyệt", 1.0, ""
        item.review = "pending"
        on_update(item)

    # ── Chuỗi đầy đủ cho một video ────────────────────────────────────────
    def process(self, item: VideoItem, *, on_update: Callable[[VideoItem], None],
                log: LogCb, cancel: threading.Event, bands: dict | None = None) -> Path:
        st = self.state
        if bands is None:
            bands, item.progress = step_bands(self.planned_steps(item, "render")), 0.0
        item.status, item.message, item.output_path = "Đang xử lý", "Đọc thông tin", None
        on_update(item)
        self.probe(item)
        info = self.ffmpeg.probe(item.path)
        if info.width <= 0 or info.height <= 0:
            raise RuntimeError("File không có luồng hình (audio thuần) — chỉ xử lý được video.")
        self.ensure_hardsub(item, info, log, cancel)
        mods = stages_mod.collect(st, item, info)
        trim = mods.trim
        # "Xuất mỗi đoạn thành 1 file": render lần lượt từng đoạn thành file riêng
        if trim.active and st.trim.mode == TRIM_MODES[1] and len(trim.segments) > 1:
            outs: list[Path] = []
            for si, seg in enumerate(trim.segments):
                sub_trim = rp.TrimSpec(segments=[seg])
                log(f"Đoạn {si + 1}/{len(trim.segments)}: {seg[0]:.1f}s → {seg[1]:.1f}s")
                outs.append(self._render_one(item, info, sub_trim, on_update=on_update, log=log, cancel=cancel,
                                             suffix=f"_p{si + 1}", final=False, bands=bands))
            item.output_path = str(outs[-1])
            item.status, item.progress, item.message = "Xong", 1.0, f"{len(outs)} file"
            on_update(item)
            return outs[-1]
        return self._render_one(item, info, trim, on_update=on_update, log=log, cancel=cancel, bands=bands)

    def voice_now(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                  cancel: threading.Event | None = None) -> Path:
        """Nút «Tạo giọng» ở tab Lồng tiếng: tạo track theo ĐÚNG đường của Chuẩn bị (Cắt, khớp thời lượng, rút gọn câu
        tràn) → Chuẩn bị / xuất sau đó dùng lại, không đọc lại. Thiếu phụ đề / bản dịch thì làm luôn như Chuẩn bị.
        Không đổi trạng thái hàng đợi, không khử giọng gốc."""
        log = log or (lambda _m: None)
        self.probe(item)
        info = self.ffmpeg.probe(item.path)
        if info.width <= 0 or info.height <= 0:
            raise RuntimeError("File không có luồng hình (audio thuần) — chỉ xử lý được video.")
        trim = stages_mod.collect(self.state, item, info).trim
        trims = [rp.TrimSpec(segments=[seg]) for seg in trim.segments] \
            if trim.active and self.state.trim.mode == TRIM_MODES[1] and len(trim.segments) > 1 else [trim]
        shadow = dataclasses.replace(item, progress=0.0)      # tiến độ của nút này, không đụng dòng hàng đợi
        steps = [k for k in self.planned_steps(item, "prepare") if k != "novocal"]
        if "voice" not in steps:
            steps.append("voice")
        stage = self._stager(shadow, lambda it: progress(it.progress) if progress else None, step_bands(steps))
        voice: Path | None = None
        for t in trims:
            _o, _tr, voice = self._prepare_assets(item, info, t, vcfg=self.state.video, stage=stage, log=log,
                                                  cancel=cancel, on_update=lambda _it: None, voice_only=True,
                                                  force_voice=True)
        if voice is None:
            raise RuntimeError("Không tạo được giọng — lồng tiếng đang tắt, hoặc video không có lời thoại.")
        return voice

    def needs(self, item: VideoItem) -> set[str]:
        """Trước khi chạy: video này sẽ cần gì mà máy có thể thiếu — «gemini» (phải dịch, chưa có bản dịch) ·
        «qwen» (phải nghe lời thoại). Cùng điều kiện với ``_prepare_assets``."""
        scfg, tcfg = self.state.subtitle, self.state.tts
        sub_on, tts_on = scfg.enable_subtitle, tcfg.enable_tts
        if not (sub_on or tts_on):
            return set()
        out = set()
        if not (item.srt_path and Path(item.srt_path).exists()) and scfg.srt_source == SRT_SOURCES[0]:
            out.add("qwen")
        want_tr = (sub_on and scfg.srt_mode in (SRT_MODES[1], SRT_MODES[2])) or \
                  (tts_on and tcfg.tts_source == TTS_SOURCES[0])
        has_tr = bool(item.srt_translated_path and Path(item.srt_translated_path).exists())
        if want_tr and not has_tr and scfg.translate_enable:
            out.add("gemini")
        elif want_tr and not has_tr:
            out.add("translate_off")                 # cần bản dịch nhưng «Tự dịch khi chuẩn bị video» đang tắt
        return out

    def _prepare_assets(self, item: VideoItem, info, trim, *, vcfg, stage, log, cancel, on_update,
                        preview_out: Path | None = None, voice_only: bool = False,
                        force_voice: bool = False) -> tuple[list[Cue], list[Cue] | None, Path | None]:
        """Phần «chuẩn bị» của một video: phụ đề (nhận diện / file SRT), bản dịch, người nói, track giọng lồng.

        Kết quả lưu ra file (SRT, WAV theo chữ ký cấu hình) → bước render sau dùng lại, không làm lại."""
        st = self.state
        scfg, tcfg = st.subtitle, st.tts
        trim_key = repr([(round(a, 3), round(b, 3)) for a, b in trim.segments]) if trim.active else ""
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
                try:
                    cues_orig = self.transcribe(item, progress=stage("Nhận diện giọng nói", 0.0, 0.3), log=log,
                                                cancel=cancel)
                except NoSpeech as exc:
                    # video chỉ có nhạc / không có tiếng: không có gì để làm phụ đề, lồng tiếng → vẫn xuất phần hình
                    log(f"⚠ {item.name}: {exc} Bỏ phụ đề + lồng tiếng, vẫn xuất video.")
                    sub_on = tts_on = need_cues = want_translated = False
                on_update(item)
            else:
                guess = Path(item.path).with_suffix(".srt")
                if guess.exists():
                    item.srt_path = str(guess)
                    cues_orig = load_srt(guess)
                    log(f"Dùng SRT cạnh video: {guess.name}")
                else:
                    raise RuntimeError("Chưa chọn file SRT cho video này.")
            if not cues_orig and need_cues:
                raise RuntimeError(f"File phụ đề {Path(item.srt_path or '').name} không đọc được câu nào — mở bằng "
                                   "Notepad, lưu lại dạng UTF-8 rồi Thử lại.")
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
        locked = tts_on and item.voice_locked and bool(item.voice_path) and Path(item.voice_path or "").exists()
        if locked:
            # «Dùng giọng có sẵn» ở tab Lồng tiếng: giữ nguyên giọng, không đọc lại (chỉ đổi nhạc / phụ đề / hiệu ứng)
            voice_path = Path(item.voice_path)
            log(f"Dùng giọng có sẵn (chọn ở tab Lồng tiếng): {voice_path.name} — không tạo giọng mới.")
            if trim.active:
                log("⚠ Đang bật Cắt mà giọng có sẵn dựng theo video chưa cắt — giọng có thể lệch hình.")
        elif tts_on:
            item.voice_locked = False                     # file giọng đã chọn không còn → tạo giọng như thường
            use_tr = tcfg.tts_source == TTS_SOURCES[0] and bool(cues_tr)
            if use_tr and tcfg.tts_provider.startswith("VieNeu") and scfg.translate_target != "Tiếng Việt":
                log(f"⚠ VieNeu chỉ đọc tiếng Việt mà bản dịch là {scfg.translate_target} — chọn Edge TTS + giọng đúng "
                    "ngôn ngữ ở tab Lồng tiếng.")

            cut_idx: set[int] = set()      # câu bị Cắt / Bỏ đầu cắt mất một phần (chữ vẫn cả câu) → không nhờ AI rút gọn

            def build_voice(cues_src: list[Cue]) -> Path:
                src_path = item.srt_translated_path if use_tr else item.srt_path
                # nhớ chỉ số cue trong SRT (qua cắt / dồn đoạn vẫn còn) → câu tràn khung báo đúng câu để dịch lại
                src_cues = [Cue(c.index, c.start, c.end, c.text, {**c.meta, "src_idx": k}) for k, c in enumerate(cues_src)]
                if tcfg.multi_voice:
                    self.ensure_speakers(item, progress=stage("Nhận diện người nói", 0.45, 0.05), log=log, cancel=cancel)
                    self.annotate_speakers(item, src_cues)
                if self.mark_noread(item, src_cues):
                    log(f"{sum(1 for c in src_cues if c.meta.get('noread'))} câu đánh dấu «không đọc» — giữ âm gốc ở đó.")
                src_cues = rp.remap_cues_for_trim(src_cues, trim)
                cut_idx.update(c.meta["src_idx"] for c in src_cues if c.meta.get("cut"))
                fit = trim.kept_duration if trim.active else info.duration      # thời lượng nguồn (÷ tốc độ khi dựng)
                return self.generate_voice(item, src_cues, srt_path=src_path or "", extra_key=trim_key,
                                           progress=stage("Tạo giọng đọc", 0.45, 0.2), log=log, cancel=cancel,
                                           fit_duration=fit if not (vcfg.loop_to_voice or preview_out) else None,
                                           out_dir=PREVIEW_CACHE_DIR / "voice" if preview_out else None,
                                           force=force_voice)

            voice_path = build_voice(cues_tr if use_tr else cues_orig)
            # câu ≤ 3 tiếng («Không bắt», «Trắng tinh») không rút gọn được nữa — nhờ AI chỉ tốn lượt dịch + lượt đọc lại
            locks = self.line_locks(item.srt_translated_path)
            held = locks["shortened"] | locks["edited"]           # đã rút gọn / người dùng tự sửa → không đụng
            over = [o for o in self.voice_overflow(voice_path, untried=True) if o[1] > 0.15
                    and cues_tr and 0 <= o[0] < len(cues_tr) and len(cues_tr[o[0]].plain.split()) > 3
                    and o[0] not in cut_idx
                    and not (o[0] < len(cues_orig) and self.line_key(cues_orig[o[0]].plain) in held)]
            # ngân sách < 6 ký tự = khung quá ngắn so với câu (thường do cắt): rút gọn kiểu gì cũng mất nghĩa → bỏ
            budgets = {i: b for i, d, slot in over
                       if (b := int(len(cues_tr[i].plain) * slot / (slot + d) * 0.9)) >= 6}
            # bản dịch đã gộp / xoá câu (số câu khác bản gốc) → chỉ số câu không khớp, không rút gọn từng câu
            same_count = bool(cues_tr) and len(cues_tr) == len(cues_orig)
            if (budgets and same_count and use_tr and preview_out is None and scfg.translate_enable
                    and item.srt_path and Path(item.srt_path).exists()):
                # đã nén tới trần mà vẫn tràn → câu dịch quá dài: AI rút gọn đúng các câu đó (một lượt) rồi đọc lại
                from app.services.translate_router import SHORTEN_REQUEST

                log(f"{len(budgets)} câu đọc tràn khung dù đã nén tối đa → AI rút gọn bản dịch rồi đọc lại…")
                try:
                    new = self.retranslate(item, sorted(budgets), instruction=SHORTEN_REQUEST, budgets=budgets,
                                           log=log, cancel=cancel)
                except RenderCancelled:
                    raise
                except Exception as exc:
                    new = None
                    log(f"⚠ Không rút gọn được bản dịch ({str(exc)[:120]}) — giữ giọng hiện tại.")
                if new and item.srt_translated_path:
                    cues_tr = load_srt(item.srt_translated_path)
                    force_voice = False                       # SRT vừa đổi → chữ ký mới, khỏi ép đọc lại lần hai
                    voice_path = build_voice(cues_tr)
                    left = [o for o in self.voice_overflow(voice_path) if o[1] > 0.15]
                    log(f"Đã rút gọn {len(new)} câu · còn {len(left)} câu tràn khung"
                        + (" (video giữ khung cuối cho đủ giọng nếu cần)" if left else ""))
                if new is not None:                 # lỗi mạng → lần sau thử lại; đã gọi được → không gọi lại
                    self.mark_shortened(voice_path, budgets)
                    self.add_line_locks(item.srt_translated_path, "shortened",
                                        [cues_orig[i].plain for i in budgets if i < len(cues_orig)])
            on_update(item)
            if voice_path is not None and sub_on and scfg.karaoke:
                # chữ nổi: căn mốc từng chữ theo giọng một lần (lưu cạnh track) → khung xem trước cũng khớp
                try:
                    word_timing.ensure(voice_path, self.ffmpeg, use_model=True, log=log, cancel=cancel)
                except RenderCancelled:
                    raise
                except Exception as exc:
                    logger.exception("Không căn được mốc chữ nổi")
                    log(f"⚠ Không căn được mốc chữ nổi ({str(exc)[:120]}) — dùng mốc ước lượng.")

        if self.wants_voice_removal(info) and not voice_only:
            self.remove_voice(item, progress=stage("Khử giọng gốc", 0.65, 0.3), log=log, cancel=cancel)
            on_update(item)

        return cues_orig, cues_tr, voice_path

    # ── Khử giọng nói gốc (giữ nhạc nền + tiếng động) ─────────────────────
    def wants_voice_removal(self, info) -> bool:
        v = self.state.video
        return bool(v.demucs_enable and v.keep_original_audio and not v.mute and getattr(info, "has_audio", False))

    def remove_voice(self, item: VideoItem, *, progress: ProgressCb | None = None, log: LogCb | None = None,
                     cancel: threading.Event | None = None) -> Path | None:
        """Âm gốc đã khử giọng nói (AI, cache theo video) hoặc None nếu không làm được."""
        from app.services.vocal_remove import separate_no_vocals

        item.message = "Khử giọng gốc (AI)"
        return separate_no_vocals(item.path, self.ffmpeg, log=log, progress=progress, cancel=cancel)

    def _keep_original_at_noread(self, item: VideoItem, novocal: Path, job_dir: Path, log: LogCb) -> Path:
        """Câu «không đọc» (lời bài hát…): ở đúng các khoảng đó dùng âm gốc còn giọng thay cho bản đã khử giọng."""
        try:
            srt = self._tts_srt_path(item)
        except RuntimeError:
            return novocal
        starts = self.load_noread(item)
        spans = [(c.start, c.end) for c in load_srt(srt) if starts and self.is_noread(c, starts)]
        if not spans:
            return novocal
        inside = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
        fmt = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
        graph = (f"[0:a]{fmt},volume=0:enable='{inside}'[nv];"
                 f"[1:a:0]{fmt},volume=0:enable='not({inside})'[og];"
                 "[nv][og]amix=inputs=2:normalize=0:duration=first[a]")
        out = job_dir / "override_noread.wav"
        res = self.ffmpeg.run_simple(["-i", str(novocal), "-i", item.path, "-filter_complex", graph, "-map", "[a]",
                                      str(out)], timeout=900)
        if res.returncode != 0 or not out.exists():
            log("⚠ Không giữ được âm gốc ở câu «không đọc» — dùng bản đã khử giọng.")
            return novocal
        log(f"Giữ âm gốc ở {len(spans)} câu «không đọc».")
        return out

    def _render_one(self, item: VideoItem, info, trim, *, on_update, log, cancel, suffix: str = "",
                    final: bool = True, preview_out: Path | None = None, bands: dict | None = None) -> Path:
        """``final=False``: một đoạn trong chế độ xuất nhiều file — không đánh dấu "Xong" để bộ đếm tiến độ
        của hàng đợi không nhảy trước. ``preview_out``: bản render thử (mã hoá nhanh, giọng/kết quả để trong cache)."""
        st = self.state
        vcfg, scfg, tcfg = st.video, st.subtitle, st.tts
        audio_only = self.audio_only and preview_out is None
        if preview_out is not None:
            vcfg = dataclasses.replace(vcfg, encode_preset=ENCODE_PRESETS[0], loop_to_voice=False)
        mods = stages_mod.collect(st, item, info, trim_override=trim)
        if bands is None:                              # xem thử: tiến độ riêng theo bước của bản thử
            bands = step_bands(self.planned_steps(item, "render"))
        stage = self._stager(item, on_update, bands)

        if trim.active:
            log(f"Cắt: giữ {len(trim.segments)} đoạn · {trim.kept_duration:.1f}s")
        elif st.trim.enable:
            log("⚠ Cắt đang bật nhưng mốc không hợp lệ (vượt thời lượng, đầu ≥ cuối…) — giữ nguyên video.")

        sub_on = scfg.enable_subtitle
        tts_on = tcfg.enable_tts
        cues_orig, cues_tr, voice_path = self._prepare_assets(item, info, trim, vcfg=vcfg, stage=stage, log=log,
                                                              cancel=cancel, on_update=on_update, preview_out=preview_out)

        job_dir = self.job_dir(item)
        job_dir.mkdir(parents=True, exist_ok=True)
        speed = max(0.25, min(4.0, vcfg.speed / 100.0))
        tspeed = rp.voice_timeline_speed(voice_path)      # track mới dựng theo mốc video ra; track cũ = 1 (mốc nguồn)
        ass_name: str | None = None
        if sub_on and cues_orig:
            sub_cues = select_sub_cues(scfg, cues_orig, cues_tr)
            sub_cues = rp.scale_cues(rp.remap_cues_for_trim(sub_cues, trim), 1.0 / speed)
            synced_srt = voice_path.with_suffix(".srt") if voice_path else None
            same_source = tts_on and _same_source(scfg, tcfg)
            if synced_srt and synced_srt.exists() and same_source:
                synced = load_srt(synced_srt)
                if scfg.srt_mode == SRT_MODES[2] and cues_tr and len(cues_orig) == len(synced):
                    # song ngữ: bản dịch (mốc theo giọng) + dòng lời gốc cùng câu
                    synced = merge_bilingual(synced, cues_orig)
                if scfg.karaoke:                                 # chữ nổi: mốc từng chữ theo giọng thật
                    synced = word_timing.attach(synced, voice_path, self.ffmpeg, use_model=True, log=log, cancel=cancel)
                sub_cues = rp.scale_cues(shift(synced, scfg.sub_delay), tspeed / speed)
                log("Phụ đề dùng mốc thời gian đã đồng bộ theo giọng đọc.")
            for c in sub_cues:                                   # mốc lời thật cho chữ nổi (karaoke)
                c.meta["anim"] = word_timing.speech_span(c)
            sub_cues = ensure_min_duration(sub_cues, 1.0)        # câu ngắn không chớp tắt (< 1 s)
            geo = rp.output_geometry(mods.info, vcfg)
            write_ass(sub_cues, stages_mod.subtitle_for(self.state, item, info), geo.width, geo.height,
                      job_dir / "sub.ass")
            ass_name = "sub.ass"

        ext = ".mp3" if audio_only else ".mp4"
        out_path = preview_out or self._free_output(item, self.output_path(item, suffix, ext))
        # độ dài giọng theo mốc video ra (track cũ theo mốc nguồn còn atempo theo tốc độ video khi render)
        voice_dur = self.ffmpeg.duration_of(voice_path) * tspeed / speed if voice_path else 0.0
        audio_override: Path | None = None
        if self.wants_voice_removal(info):
            on_update(item)
            audio_override = self.remove_voice(item, log=log, cancel=cancel)       # đã làm ở bước chuẩn bị → cache
            if audio_override is None:
                # không khử được (thiếu model / lỗi) → vẫn đúng ý «giọng gốc rất nhỏ»: hạ âm gốc xuống tối đa 8%
                vcfg = dataclasses.replace(vcfg, original_volume=min(vcfg.original_volume, 8.0))
                log("⚠ Không khử được giọng gốc — hạ âm gốc xuống ≤ 8%.")
            elif tts_on:
                audio_override = self._keep_original_at_noread(item, audio_override, job_dir, log)
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
        if st.color.dup_gop and not vcfg.gop_size:
            vcfg = dataclasses.replace(vcfg, gop_size=47)
        item.message = "Ghép timeline"
        on_update(item)
        plan = rp.plan_render(item, mods.info, vcfg, ffmpeg=self.ffmpeg, job_dir=job_dir, out_path=out_path,
                              ass_name=ass_name, voice_path=voice_path, stages=mods.stages,
                              pre_stages=mods.pre_stages, src_stages=mods.src_stages, trim=trim, audio_extras=mods.audio_extras,
                              voice_duration=voice_dur, audio_only=audio_only, audio_override=audio_override,
                              audio_orig_filters=mods.audio_orig_filters, log=log, voice_tempo=speed / tspeed,
                              loudness=st.settings.loudness_target)
        log(f"Xuất thử → {out_path.name}" if preview_out else f"Xuất → {out_path.name}")
        rp.run_plan(plan, self.ffmpeg, on_progress=stage("Render", 0.65, 0.35), cancel=cancel, log=log)
        for tmp in (plan.mix_path, plan.master_path):    # WAV tạm lớn, chỉ dùng cho lần xuất này
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)
        m = plan.master_info
        if m.get("output_i") is not None:
            log((f"Âm thanh: {m['input_i']:.1f} → {m['output_i']:.1f} LUFS (đích {plan.loudness:.0f})"
                 if plan.loudness is not None and m.get("input_i") is not None
                 else f"Âm thanh: {m['output_i']:.1f} LUFS (không chuẩn hoá độ to)")
                + f" · đỉnh {m.get('output_tp', 0.0):.1f} dBTP (giới hạn {rp.MASTER_TP:.1f})")
        item.output_path = str(out_path)
        if final:
            item.status, item.progress, item.message = "Xong", 1.0, out_path.name
        else:
            item.progress, item.message = 1.0, out_path.name
        on_update(item)
        return out_path


def error_line(exc: BaseException, limit: int = 260) -> str:
    """Dòng đầu của câu lỗi cho người dùng — lỗi không có chữ (MemoryError, TimeoutError…) thì ghi tên loại lỗi.
    Dài quá thì cắt ở dấu cách gần nhất + «…» (không cắt giữa chữ)."""
    lines = str(exc).strip().splitlines()
    names = {"MemoryError": "Hết bộ nhớ RAM (video quá dài?)", "TimeoutError": "Quá thời gian chờ"}
    text = lines[0] if lines else names.get(type(exc).__name__, type(exc).__name__)
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    return text[:cut if cut > limit // 2 else limit - 1].rstrip(" ,.;:—-") + "…"


class JobRunner:
    """Chạy hàng đợi trong thread nền; mỗi lúc chỉ một hàng đợi. Dừng tất cả (``stop``) hoặc bỏ qua một video
    (``skip``): mỗi video một cờ huỷ riêng — bỏ qua video đang chạy thì các video sau vẫn chạy tiếp."""

    def __init__(self, pipeline: Pipeline, ui_call: Callable[[Callable[[], None]], None]) -> None:
        self.pipeline = pipeline
        self.ui_call = ui_call
        self._thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.current: VideoItem | None = None
        self._item_cancel = threading.Event()
        self._skipped: set[int] = set()
        self._bands: dict[int, dict] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stopping(self) -> bool:
        return self.running and self.cancel.is_set()

    def skip(self, item: VideoItem) -> None:
        """Bỏ qua ``item`` trong lượt đang chạy: đang xử lý → dừng video đó; chưa tới lượt → không làm."""
        self._skipped.add(id(item))
        if self.current is item:
            self._item_cancel.set()

    def start(self, items: list[VideoItem], *, on_update: Callable[[VideoItem], None],
              on_log: LogCb, on_done: Callable[[int, int], None], mode: str = "render") -> bool:
        """``mode``: "render" (xuất video) · "prepare" (chỉ chuẩn bị phụ đề / dịch / giọng → «Chờ duyệt»)."""
        if self.running or not items:
            return False
        self.cancel.clear()
        self._skipped.clear()
        # mỗi video một bản sao cấu hình (của mẫu / cấu hình riêng) — chụp lúc bấm Render, UI đổi gì sau đó không ảnh hưởng
        base = self.pipeline
        jobs = [(it, base.state.state_for(it)) for it in items]
        # dải tiến độ theo đúng các bước từng video sẽ chạy (bước nghe lời thoại chung chạy trước cũng theo dải này)
        self._bands = {}
        for it, st in jobs:
            try:
                self._bands[id(it)] = step_bands(Pipeline(st, base.ffmpeg).planned_steps(it, mode))
            except Exception:                               # noqa: BLE001 — không tính được thì dải cũ của pipeline
                logger.debug("Không tính được bước của %s", it.name, exc_info=True)

        def _ui_update(it: VideoItem) -> None:
            self.ui_call(lambda: on_update(it))

        def _ui_log(msg: str) -> None:
            logger.info(msg)
            self.ui_call(lambda: on_log(msg))

        def run() -> None:
            ok = fail = 0
            try:
                self._listen_first(jobs, base, mode, _ui_update, _ui_log)
                blocked = ""                               # câu báo model hết lượt / bị gỡ (gặp ở video trước)
                for it, st in jobs:
                    if self.cancel.is_set() or id(it) in self._skipped:
                        it.status, it.message = ("Đã huỷ", "") if self.cancel.is_set() else ("Đã bỏ qua", "")
                        _ui_update(it)
                        continue
                    if blocked and "gemini" in Pipeline(st, base.ffmpeg).needs(it):
                        it.status, it.message = "Lỗi", blocked
                        _ui_update(it)
                        _ui_log(f"⏭ {it.name}: chưa chạy — model Gemini không dùng được (đổi model rồi Thử lại).")
                        fail += 1
                        continue
                    self._item_cancel = ev = threading.Event()        # cờ riêng video này (Dừng tất cả cũng bật cờ)
                    self.current = it
                    if self.cancel.is_set():
                        ev.set()
                    try:
                        pipe = Pipeline(st, base.ffmpeg)
                        pipe.audio_only = base.audio_only
                        if it.config is not None or it.template:
                            _ui_log(f"{it.name}: dùng cấu hình riêng" + (f" (mẫu «{it.template}»)" if it.template else ""))
                        bands = self._bands.get(id(it))
                        if mode == "prepare":
                            pipe.prepare(it, on_update=_ui_update, log=_ui_log, cancel=ev, bands=bands)
                        else:
                            pipe.process(it, on_update=_ui_update, log=_ui_log, cancel=ev, bands=bands)
                        ok += 1
                    except RenderCancelled:
                        skipped = not self.cancel.is_set()
                        it.status, it.message = ("Đã bỏ qua", "") if skipped else ("Đã huỷ", "")
                        _ui_update(it)
                        if skipped:
                            _ui_log(f"⏭ Bỏ qua {it.name} — chạy tiếp video sau.")
                    except Exception as exc:
                        logger.exception("Lỗi xử lý %s", it.name)
                        from app.services.translate_router import ModelUnavailable

                        if isinstance(exc, ModelUnavailable):
                            blocked = error_line(exc)
                        it.status, it.message = "Lỗi", error_line(exc)
                        _ui_update(it)
                        _ui_log(f"✖ {it.name}: {error_line(exc, 400)}")
                        fail += 1
            except BaseException:                      # không để thanh trạng thái kẹt «Đang xử lý» mãi
                logger.exception("Hàng đợi dừng bất thường")
            finally:
                self.current = None
                self.ui_call(lambda: on_done(ok, fail))

        self._thread = threading.Thread(target=run, name="JobRunner", daemon=True)
        self._thread.start()
        return True

    def _listen_first(self, jobs: list, base: Pipeline, mode: str, ui_update: Callable[[VideoItem], None],
                      ui_log: LogCb) -> None:
        """≥ 2 video cần nghe lời thoại (Qwen3-ASR) → nghe hết trước bằng MỘT tiến trình (nạp model một lần cho cả
        lượt), xong mới dịch / lồng tiếng từng video — model đã tắt nên không tranh GPU với bước lồng tiếng. Video nào
        lỗi / chưa nghe xong thì vòng chính nghe riêng như cũ (báo lỗi đúng video)."""
        from app.services import qwen_asr

        todo = []
        for it, st in jobs:
            pipe = Pipeline(st, base.ffmpeg)
            if id(it) not in self._skipped and "qwen" in pipe.needs(it) and not pipe.silent(it.path):
                todo.append((it, pipe))
        if len(todo) < 2 or self.cancel.is_set() or not qwen_asr.is_installed():
            return
        running = "Đang chuẩn bị" if mode == "prepare" else "Đang xử lý"
        batch = [qwen_asr.BatchJob(it.path, pipe.job_dir(it), pipe.state.subtitle.stt_language, it.name,
                                   Pipeline._stager(it, ui_update, self._bands.get(id(it)))("Nhận diện giọng nói", 0.0, 0.3))
                 for it, pipe in todo]

        def on_current(k: int) -> None:
            prev, it = self.current, todo[k][0]
            if prev is not None and prev is not it and prev.status == running:
                prev.status = "Chờ"                     # đã nghe xong, chờ lượt dịch / lồng tiếng
                ui_update(prev)
            self.current = it
            it.status, it.message = running, "Nhận diện giọng nói"
            ui_update(it)

        # video có phụ đề in sẵn: đọc chữ (CPU) song song lúc Qwen nghe (GPU) → sửa chữ nghe sai theo chữ trên hình
        hard: dict[int, list[Cue] | None] = {}

        def read_all() -> None:
            for k, (it, pipe) in enumerate(todo):
                if self.cancel.is_set():
                    return
                if id(it) not in self._skipped:
                    try:
                        hard[k] = pipe.hardsub_cues(it, log=ui_log, cancel=self.cancel)
                    except RenderCancelled:
                        return

        reader = threading.Thread(target=read_all, name="OCR", daemon=True)
        reader.start()
        results: list = [None] * len(todo)
        try:
            results = qwen_asr.transcribe_batch(batch, base.ffmpeg, settings=base.state.settings, log=ui_log,
                                                cancel=self.cancel, skipped=lambda k: id(todo[k][0]) in self._skipped,
                                                on_current=on_current)
        except RenderCancelled:
            pass
        except Exception as exc:                        # noqa: BLE001 — vòng chính nghe riêng từng video
            logger.warning("Nghe lời thoại nhiều video lỗi", exc_info=True)
            ui_log(f"⚠ Nghe lời thoại chung lỗi ({error_line(exc)}) — nghe riêng từng video.")
        finally:
            self.current = None
            for it, _pipe in todo:
                if it.status == running:
                    it.status = "Chờ"
                    ui_update(it)
        reader.join()
        for k, ((it, pipe), res) in enumerate(zip(todo, results)):
            if res is not None and id(it) not in self._skipped:
                if hard.get(k):
                    try:
                        res = pipe.with_hardsub(it, res, hard[k], log=ui_log, cancel=self.cancel)
                    except RenderCancelled:
                        break
                try:
                    pipe.save_transcript(it, res, ui_log)
                except NoSpeech:                        # đã ghi dấu «không có lời» → vòng chính bỏ phụ đề, vẫn xuất
                    pass
                except Exception:                       # noqa: BLE001 — chưa lưu được → vòng chính nghe lại
                    logger.warning("Lưu phụ đề %s lỗi", it.name, exc_info=True)

    def stop(self) -> None:
        self.cancel.set()
        self._item_cancel.set()


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
