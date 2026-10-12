"""Nhận diện bằng Qwen3-ASR + Qwen3-ForcedAligner (như VideoLingo bản 01f8434) — chạy trong môi trường Python riêng
models/qwen_asr_env (torch 2.8 + qwen-asr 0.0.6 + transformers 4.57 + spaCy 3.8, ~6 GB) để không đụng thư viện của app;
model tải vào models/hf (~5,5 GB, lần đầu). app gọi qwen_asr_worker.py bằng python của môi trường đó, đọc tiến độ.
Sau đó (trong app): AI tách câu > 20 token (translate_vl.split_long_sentences) → mốc câu theo mốc từng chữ, khoảng hở
< 1 s nối vào câu trước (_6_gen_sub của VideoLingo). Nhiều video một lượt: ``transcribe_batch`` — một tiến trình, nạp model
một lần (JobRunner gọi trước khi dịch / lồng tiếng, xong thì tiến trình tắt, trả GPU cho bước lồng tiếng)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config.paths import JOBS_DIR, MODELS_DIR
from app.core.state import Settings
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, wrap_lines

logger = logging.getLogger(__name__)

ENV_DIR = MODELS_DIR / "qwen_asr_env"
HF_DIR = MODELS_DIR / "hf"
WORKER = Path(__file__).with_name("qwen_asr_worker.py")
READY = ENV_DIR / "ready.json"
TORCH = ("torch==2.8.0", "torchaudio==2.8.0")
PACKAGES = ("qwen-asr==0.0.6", "transformers>=4.57.6,<5", "spacy>=3.8,<3.9", "silero-vad>=6.2,<6.3", "soundfile")
ZH_SPACY = "https://github.com/explosion/spacy-models/releases/download/zh_core_web_md-3.8.0/zh_core_web_md-3.8.0-py3-none-any.whl"
INSTALL_GB = 11                          # ~6 GB môi trường (torch CUDA) + ~5,5 GB model lần đầu nhận diện
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
#: dấu ở đầu câu lỗi «chưa cài» — giao diện thấy dấu này thì hiện nút «Cài…» (mở Cài đặt › AI trên máy)
MISSING_MARK = "⟦cài-ai-trên-máy⟧"


def env_python() -> Path:
    return ENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_installed() -> bool:
    return env_python().exists() and READY.exists()


def _has_nvidia() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:                                    # noqa: BLE001
        return False


_ACTIVE: set[subprocess.Popen] = set()


def kill_all() -> None:
    """Đóng app: tắt mọi tiến trình Qwen3-ASR / pip đang chạy."""
    for proc in list(_ACTIVE):
        _kill_tree(proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Dừng pip cùng các tiến trình con của nó (Windows: taskkill /T)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=_NO_WINDOW)
        else:
            proc.terminate()
    except OSError:
        pass


def _step(cmd: list[str], cancel: threading.Event | None) -> tuple[int | None, str]:
    """Chạy một bước cài; bấm Dừng → dừng ngay (không chờ pip tải xong 3,5 GB). Trả (mã thoát | None nếu bị dừng, lỗi)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW)
    _ACTIVE.add(proc)
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)
                return proc.returncode, (err or out or "")
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    _kill_tree(proc)
                    proc.communicate()
                    return None, ""
    finally:
        _ACTIVE.discard(proc)


def _pip_reason(err: str) -> str:
    """Đuôi lỗi pip → lý do người dùng hiểu được."""
    low = err.lower()
    if "no space left" in low or "errno 28" in low:
        return "ổ đĩa đầy — giải phóng ~12 GB rồi cài lại"
    if "could not find a version" in low or "no matching distribution" in low:
        return (f"không có bản thư viện cho Python {sys.version_info.major}.{sys.version_info.minor} — cần Python "
                "3.10–3.12")
    if any(k in low for k in ("connection", "timed out", "temporary failure", "getaddrinfo", "ssl")):
        return "mất mạng / tải bị ngắt — kiểm tra mạng rồi cài lại (phần đã tải được dùng lại)"
    lines = [ln.strip() for ln in err.strip().splitlines() if ln.strip()]
    return lines[-1][:160] if lines else "không rõ — xem log"


def install(log: Callable[[str], None] | None = None, cancel: threading.Event | None = None,
            on_step: Callable[[int, int, str], None] | None = None) -> bool:
    """Tạo môi trường riêng + cài thư viện (vài phút, tải ~3,5 GB torch nếu có GPU NVIDIA). Chạy trong luồng nền.
    Lỗi → RuntimeError kèm lý do; bấm Dừng → RenderCancelled (dừng pip ngay, cài lại sẽ làm tiếp).
    ``on_step(k, n, tên bước)``: báo bước đang làm (thanh trạng thái)."""
    if getattr(sys, "frozen", False):
        raise RuntimeError("Bản đóng gói (.exe / .app) không cài thêm được Qwen3-ASR — chạy bản mã nguồn "
                           "(run.bat / run.command).")
    gpu = _has_nvidia()
    index = "https://download.pytorch.org/whl/cu128" if gpu else "https://download.pytorch.org/whl/cpu"
    py = str(env_python())
    steps = [([sys.executable, "-m", "venv", str(ENV_DIR)], "tạo môi trường riêng"),
             ([py, "-m", "pip", "install", "--upgrade", "pip"], "cập nhật pip"),
             ([py, "-m", "pip", "install", *TORCH, "--index-url", index], f"torch 2.8 ({'GPU CUDA' if gpu else 'CPU'})"),
             ([py, "-m", "pip", "install", *PACKAGES, ZH_SPACY], "qwen-asr, transformers, spaCy tiếng Trung, Silero VAD")]
    for k, (cmd, what) in enumerate(steps, 1):
        if on_step:
            on_step(k, len(steps) + 1, what)
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ cài Qwen3-ASR")
        if cmd[0] != sys.executable and not Path(py).exists():
            raise RuntimeError("Cài Qwen3-ASR lỗi: không tạo được môi trường riêng (models/qwen_asr_env)")
        if log:
            log(f"Qwen3-ASR: {what}…")
        code, err = _step(cmd, cancel)
        if code is None:
            raise RenderCancelled("Đã huỷ cài Qwen3-ASR — bấm Cài lại sẽ làm tiếp")
        if code != 0:
            if log:
                log(f"Cài Qwen3-ASR lỗi ở bước «{what}»: {err[-600:]}")
            raise RuntimeError(f"Cài Qwen3-ASR lỗi ở bước «{what}»: {_pip_reason(err)}")
    if on_step:
        on_step(len(steps) + 1, len(steps) + 1, "chạy thử")
    check = subprocess.run([py, "-c", "import torch, qwen_asr, spacy, silero_vad; spacy.load('zh_core_web_md'); "
                            "print(torch.__version__, torch.cuda.is_available())"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if check.returncode != 0:
        if log:
            log("Cài xong nhưng chạy thử lỗi: " + check.stderr[-500:])
        raise RuntimeError("Cài xong nhưng chạy thử Qwen3-ASR lỗi: " + _pip_reason(check.stderr))
    READY.write_text(json.dumps({"torch": check.stdout.strip(), "gpu": gpu}), encoding="utf-8")
    if log:
        log(f"Đã cài Qwen3-ASR ({check.stdout.strip()}). Lần nhận diện đầu tiên sẽ tải model ~5,5 GB.")
    return True


def _rmp(t: str) -> str:
    return re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", t.lower())).strip()


def sentence_cues(words: list[dict], sentences: list[str]) -> list[Cue]:
    """get_sentence_timestamps của VideoLingo: khớp chữ của câu (bỏ dấu) vào chuỗi chữ có mốc → đầu = chữ đầu, cuối =
    chữ cuối; khoảng hở < 1 s giữa hai câu nối vào câu trước. Câu không khớp được (hiếm) → đặt liền sau câu trước."""
    full, posmap = "", {}
    for k, w in enumerate(words):
        c = _rmp(w["word"])
        s0 = len(full)
        full += c
        for p in range(s0, len(full)):
            posmap[p] = k
    stamps, cur = [], 0
    for s in sentences:
        cs = _rmp(s).replace(" ", "")
        if not cs:
            stamps.append(None)
            continue
        at = full.find(cs, cur)
        if at < 0:
            logger.warning("Qwen3-ASR: không khớp được câu «%s» với mốc chữ", s[:40])
            stamps.append(None)
            continue
        stamps.append([words[posmap[at]]["start"], words[posmap[at + len(cs) - 1]]["end"]])
        cur = at + len(cs)
    for i, st in enumerate(stamps):                      # câu chỉ có dấu / không khớp → lấp giữa hai câu kề
        if st is None:
            a = stamps[i - 1][1] if i > 0 and stamps[i - 1] else 0.0
            nxt = next((x for x in stamps[i + 1:] if x), None)
            stamps[i] = [a, max(a + 0.3, nxt[0] if nxt else a + 1.0)]
    for i in range(len(stamps) - 1):
        if 0 < stamps[i + 1][0] - stamps[i][1] < 1:
            stamps[i][1] = stamps[i + 1][0]
    return [Cue(i + 1, a, max(b, a + 0.2), wrap_lines(s.strip())) for i, (s, (a, b)) in enumerate(zip(sentences, stamps))]


def require() -> None:
    if not is_installed():
        where = ("bản .exe: chép models\\qwen_asr_env + models\\hf từ máy đã cài" if getattr(sys, "frozen", False)
                 else f"Cài đặt › AI trên máy › «Cài» (một lần, ~{INSTALL_GB} GB)")
        raise RuntimeError(f"{MISSING_MARK} Chưa cài bộ nghe lời thoại (Qwen3-ASR) — {where}. Video có phụ đề in sẵn "
                           "thì vẫn lấy được lời từ chữ trên hình.")


def _run(args: list[str], *, on_line: Callable[[str], None],
         should_stop: Callable[[], bool]) -> tuple[int, list[str], bool]:
    """Chạy worker trong môi trường riêng, chuyển từng dòng stdout cho ``on_line``; ``should_stop()`` đúng (xét mỗi
    0,5 s) → dừng tiến trình. Trả (mã thoát, vài dòng cuối, có bị dừng không)."""
    env = dict(os.environ, HF_HOME=str(HF_DIR), HF_HUB_DISABLE_SYMLINKS_WARNING="1", PYTHONIOENCODING="utf-8",
               TRANSFORMERS_VERBOSITY="error")
    proc = subprocess.Popen([str(env_python()), "-I", str(WORKER), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env,
                            creationflags=_NO_WINDOW)
    tail: list[str] = []
    stop, killed = threading.Event(), threading.Event()
    _ACTIVE.add(proc)

    def watch() -> None:
        while not stop.wait(0.25):
            if should_stop():
                killed.set()
                proc.terminate()
                return

    threading.Thread(target=watch, daemon=True).start()
    try:
        for line in proc.stdout:                         # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                tail = (tail + [line])[-12:]
                on_line(line)
        proc.wait()
    finally:
        stop.set()
        _ACTIVE.discard(proc)
    return proc.returncode, tail, killed.is_set()


def _progress(line: str) -> tuple[int, float]:
    """«PROGRESS k x» (video k) → (k, x); dòng cũ «PROGRESS x» → (0, x)."""
    parts = line.split()
    return (int(parts[1]), float(parts[2])) if len(parts) >= 3 else (0, float(parts[1]))


_MADE_UP = re.compile(r"(请|记得|欢迎).{0,4}(点赞|订阅)|点赞.{0,4}(订阅|关注|转发)|订阅.{0,6}频道|一键三连|"
                      r"(谢谢|感谢)(大家)?的?(观看|收看|收听)|字幕(由|提供|志愿者|组)|明镜与点点|"
                      r"thank(s| you) for watching|please subscribe|like and subscribe", re.I)


def _made_up(text: str) -> bool:
    """Câu ngắn (≤ 20 chữ) chỉ gồm lời «kết video» mà model hay tự bịa khi nghe đoạn chỉ có nhạc nền."""
    t = re.sub(r"[\W_]+", "", text)
    return 0 < len(t) <= 20 and bool(_MADE_UP.search(text))


def _finish(data: dict, *, settings: Settings, log: Callable[[str], None] | None,
            cancel: threading.Event | None, name: str = ""):
    """Kết quả của worker → câu có mốc: AI tách câu dài, mốc câu theo mốc từng chữ."""
    from app.services.translate_vl import split_long_sentences
    from app.services.whisper_stt import STTResult

    sents = [s for s in data["sentences"] if not _made_up(s["text"])]
    dropped = len(data["sentences"]) - len(sents)
    if dropped and log:
        log(f"Qwen3-ASR: bỏ {dropped} câu bịa (kiểu «请订阅», «谢谢观看») ở đoạn chỉ có nhạc")
    data = {**data, "sentences": sents}
    sents = [s["text"] for s in data["sentences"]]
    sents = split_long_sentences(sents, [int(s["tokens"]) for s in data["sentences"]], settings=settings,
                                 language=data.get("language", "zh"), log=log, cancel=cancel)
    cues = sentence_cues(data["words"], sents)
    if log:
        log(f"Qwen3-ASR{' · ' + name if name else ''}: {len(cues)} câu"
            + (f" · căn mốc 2 lượt {data['realigned']}/{data['chunks']} đoạn" if data.get("chunks") else "")
            + (f" · sửa {data['repaired']} vùng căn mốc sụp" if data.get("repaired") else ""))
    return STTResult(cues=cues, language=data.get("language", ""), language_probability=1.0)


def _clean(out: Path) -> None:
    for p in (Path(f"{out}.pieces.json"), Path(f"{out}.skip")):
        p.unlink(missing_ok=True)


def _listen(video_path: str | Path, ffmpeg: FFmpeg, *, language: str, work_dir: Path,
            on_progress: Callable[[float], None] | None, log: Callable[[str], None] | None,
            cancel: threading.Event | None) -> dict:
    """Chạy worker cho một video → kết quả thô {language, words, sentences…}."""
    require()
    work_dir.mkdir(parents=True, exist_ok=True)
    wav, out = work_dir / "qwen_audio.wav", work_dir / "qwen_asr.json"
    if log:
        log("Qwen3-ASR: tách âm thanh…")
    ffmpeg.extract_audio_wav(video_path, wav, sample_rate=16000)
    out.unlink(missing_ok=True)
    _clean(out)
    fail: list[str] = []

    def on_line(line: str) -> None:
        if line.startswith("PROGRESS "):
            if on_progress:
                on_progress(0.9 * _progress(line)[1])
        elif line.startswith("FAIL "):
            fail.append(line.split(" ", 2)[-1])
        elif line.startswith("[") and log:               # dòng của worker; bỏ cảnh báo của thư viện
            log("Qwen3-ASR " + line)

    code, tail, _ = _run(["--audio", str(wav), "--out", str(out), "--language", language or "auto"],
                         on_line=on_line, should_stop=lambda: cancel is not None and cancel.is_set())
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("Đã huỷ nhận diện")
    if code != 0 or not out.exists():
        raise RuntimeError("Qwen3-ASR lỗi: " + (fail[0] if fail else " | ".join(tail[-4:])[-400:]))
    data = json.loads(out.read_text(encoding="utf-8"))
    wav.unlink(missing_ok=True)
    _clean(out)
    return data


def transcribe(video_path: str | Path, ffmpeg: FFmpeg, *, language: str = "auto", settings: Settings,
               work_dir: Path, on_progress: Callable[[float], None] | None = None,
               log: Callable[[str], None] | None = None, cancel: threading.Event | None = None):
    data = _listen(video_path, ffmpeg, language=language, work_dir=work_dir, on_progress=on_progress, log=log,
                   cancel=cancel)
    res = _finish(data, settings=settings, log=log, cancel=cancel)
    if on_progress:
        on_progress(1.0)
    return res


@dataclass
class BatchJob:
    video_path: str
    work_dir: Path
    language: str = "auto"
    name: str = ""
    on_progress: Callable[[float], None] | None = None


def transcribe_batch(jobs: list[BatchJob], ffmpeg: FFmpeg, *, settings: Settings,
                     log: Callable[[str], None] | None = None, cancel: threading.Event | None = None,
                     skipped: Callable[[int], bool] = lambda k: False,
                     on_current: Callable[[int], None] | None = None) -> list:
    """Nghe nhiều video với MỘT tiến trình Qwen3-ASR (nạp model một lần cho cả lượt thay vì mỗi video một lần).
    Trả kết quả theo thứ tự ``jobs``: STTResult, hoặc None (lỗi / bị bỏ qua — bước sau nghe riêng video đó như cũ,
    báo lỗi đúng chỗ). ``skipped(k)``: người dùng bấm Bỏ qua video k — chưa tới lượt thì worker bỏ qua; đang nghe thì
    dừng tiến trình rồi chạy lại cho các video còn lại (video đã nghe xong không nghe lại). ``on_current(k)``: worker
    bắt đầu làm video k."""
    require()
    paths: list[tuple[Path, Path]] = []
    for job in jobs:
        job.work_dir.mkdir(parents=True, exist_ok=True)
        wav, out = job.work_dir / "qwen_audio.wav", job.work_dir / "qwen_asr.json"
        out.unlink(missing_ok=True)
        _clean(out)
        paths.append((wav, out))
    if log:
        log(f"Qwen3-ASR: nghe lời thoại {len(jobs)} video một lượt (nạp model một lần)…")
    ready = set()
    for k, job in enumerate(jobs):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ nhận diện")
        try:
            ffmpeg.extract_audio_wav(job.video_path, paths[k][0], sample_rate=16000)
            ready.add(k)
        except Exception:                                # noqa: BLE001 — video này để bước sau nghe riêng, báo lỗi đúng chỗ
            logger.warning("Qwen3-ASR: không tách được âm thanh %s", job.video_path, exc_info=True)
    done: set[int] = set()
    failed: set[int] = set()
    batch_file = JOBS_DIR / "qwen_batch.json"
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        pending = [k for k in sorted(ready) if k not in done and k not in failed and not skipped(k)]
        if not pending:
            break
        batch_file.write_text(json.dumps([{"audio": str(paths[k][0]), "out": str(paths[k][1]),
                                           "language": jobs[k].language or "auto"} for k in pending],
                                         ensure_ascii=False), encoding="utf-8")
        current: list[int | None] = [None]
        restart = threading.Event()

        def should_stop() -> bool:
            if cancel is not None and cancel.is_set():
                return True
            for k in pending:
                if k not in done and skipped(k):
                    if k == current[0]:
                        restart.set()                    # bỏ qua video đang nghe → dừng, chạy lại phần còn lại
                        return True
                    Path(f"{paths[k][1]}.skip").touch()  # chưa tới lượt → worker tự bỏ qua
            return False

        def on_line(line: str) -> None:
            head, _, rest = line.partition(" ")
            if head == "JOB":
                current[0] = pending[int(rest)]
                if on_current:
                    on_current(current[0])
            elif head == "PROGRESS":
                i, x = _progress(line)
                cb = jobs[pending[i]].on_progress
                if cb:
                    cb(0.9 * x)
            elif head == "DONE":
                done.add(pending[int(rest)])
            elif head == "FAIL":
                i, _, why = rest.partition(" ")
                failed.add(pending[int(i)])
                if log:
                    log(f"⚠ Qwen3-ASR · {jobs[pending[int(i)]].name}: {why} — sẽ nghe riêng video này")
            elif line.startswith("[") and log:
                who = f" · {jobs[current[0]].name}" if current[0] is not None else ""
                log(f"Qwen3-ASR{who} {line}")

        code, tail, killed = _run(["--jobs", str(batch_file)], on_line=on_line, should_stop=should_stop)
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ nhận diện")
        if not (killed and restart.is_set()):
            if code != 0:
                logger.warning("Qwen3-ASR (nhiều video) dừng bất thường: %s", " | ".join(tail[-4:]))
            break                                        # xong, hoặc tiến trình hỏng → video còn lại nghe riêng
    batch_file.unlink(missing_ok=True)
    results: list = [None] * len(jobs)
    for k in sorted(done):
        if skipped(k):
            continue
        try:
            results[k] = _finish(json.loads(paths[k][1].read_text(encoding="utf-8")), settings=settings, log=log,
                                 cancel=cancel, name=jobs[k].name)
        except RenderCancelled:
            raise
        except Exception:                                # noqa: BLE001 — để bước sau nghe riêng
            logger.warning("Qwen3-ASR: đọc kết quả %s lỗi", jobs[k].video_path, exc_info=True)
            continue
        if jobs[k].on_progress:
            jobs[k].on_progress(1.0)
    for k, (wav, out) in enumerate(paths):
        if results[k] is not None:
            wav.unlink(missing_ok=True)
        _clean(out)
    return results
