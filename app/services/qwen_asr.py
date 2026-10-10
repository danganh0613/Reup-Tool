"""Nhận diện bằng Qwen3-ASR + Qwen3-ForcedAligner (như VideoLingo bản 01f8434) — chạy trong môi trường Python riêng
models/qwen_asr_env (torch 2.8 + qwen-asr 0.0.6 + transformers 4.57 + spaCy 3.8, ~6 GB) để không đụng thư viện của app;
model tải vào models/hf (~5,5 GB, lần đầu). app gọi qwen_asr_worker.py bằng python của môi trường đó, đọc tiến độ.
Sau đó (trong app): AI tách câu > 20 token (translate_vl.split_long_sentences) → mốc câu theo mốc từng chữ, khoảng hở
< 1 s nối vào câu trước (_6_gen_sub của VideoLingo)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from app.config.paths import MODELS_DIR
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


def install(log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> bool:
    """Tạo môi trường riêng + cài thư viện (vài phút, tải ~3,5 GB torch nếu có GPU NVIDIA). Chạy trong luồng nền."""
    if getattr(sys, "frozen", False):
        if log:
            log("Bản đóng gói (.exe / .app) không cài thêm được Qwen3-ASR — chạy bản mã nguồn (run.bat / run.command).")
        return False
    gpu = _has_nvidia()
    index = "https://download.pytorch.org/whl/cu128" if gpu else "https://download.pytorch.org/whl/cpu"
    py = str(env_python())
    steps = [([sys.executable, "-m", "venv", str(ENV_DIR)], "tạo môi trường riêng"),
             ([py, "-m", "pip", "install", "--upgrade", "pip"], "cập nhật pip"),
             ([py, "-m", "pip", "install", *TORCH, "--index-url", index], f"torch 2.8 ({'GPU CUDA' if gpu else 'CPU'})"),
             ([py, "-m", "pip", "install", *PACKAGES, ZH_SPACY], "qwen-asr, transformers, spaCy tiếng Trung, Silero VAD")]
    for cmd, what in steps:
        if cancel is not None and cancel.is_set():
            return False
        if cmd[0] != sys.executable and not Path(py).exists():
            return False
        if log:
            log(f"Qwen3-ASR: {what}…")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=_NO_WINDOW)
        if proc.returncode != 0:
            if log:
                log(f"Cài Qwen3-ASR lỗi ở bước «{what}»: {(proc.stderr or proc.stdout)[-600:]}")
            return False
    check = subprocess.run([py, "-c", "import torch, qwen_asr, spacy, silero_vad; spacy.load('zh_core_web_md'); "
                            "print(torch.__version__, torch.cuda.is_available())"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if check.returncode != 0:
        if log:
            log("Cài xong nhưng chạy thử lỗi: " + check.stderr[-500:])
        return False
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


def transcribe(video_path: str | Path, ffmpeg: FFmpeg, *, language: str = "auto", settings: Settings,
               work_dir: Path, on_progress: Callable[[float], None] | None = None,
               log: Callable[[str], None] | None = None, cancel: threading.Event | None = None):
    from app.services.translate_vl import split_long_sentences
    from app.services.whisper_stt import STTResult

    if not is_installed():
        raise RuntimeError(f"{MISSING_MARK} Chưa cài bộ nghe lời thoại (Qwen3-ASR) — Cài đặt › AI trên máy › «Cài» "
                           f"(một lần, ~{INSTALL_GB} GB), hoặc chọn «Chữ in sẵn trên video» nếu video có phụ đề in sẵn.")
    work_dir.mkdir(parents=True, exist_ok=True)
    wav, out = work_dir / "qwen_audio.wav", work_dir / "qwen_asr.json"
    if log:
        log("Qwen3-ASR: tách âm thanh…")
    ffmpeg.extract_audio_wav(video_path, wav, sample_rate=16000)
    env = dict(os.environ, HF_HOME=str(HF_DIR), HF_HUB_DISABLE_SYMLINKS_WARNING="1", PYTHONIOENCODING="utf-8",
               TRANSFORMERS_VERBOSITY="error")
    out.unlink(missing_ok=True)
    proc = subprocess.Popen([str(env_python()), "-I", str(WORKER), "--audio", str(wav), "--out", str(out),
                             "--language", language or "auto"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=env, creationflags=_NO_WINDOW)
    tail: list[str] = []
    stop = threading.Event()

    def watch_cancel() -> None:
        while not stop.wait(0.5):
            if cancel is not None and cancel.is_set():
                proc.terminate()
                return

    threading.Thread(target=watch_cancel, daemon=True).start()
    try:
        for line in proc.stdout:                         # type: ignore[union-attr]
            line = line.rstrip()
            if line.startswith("PROGRESS "):
                if on_progress:
                    on_progress(0.9 * float(line.split()[1]))
                continue
            if line:
                tail = (tail + [line])[-12:]
                if line.startswith("[") and log:         # dòng của worker; bỏ cảnh báo của thư viện
                    log("Qwen3-ASR " + line)
        proc.wait()
    finally:
        stop.set()
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("Đã huỷ nhận diện")
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError("Qwen3-ASR lỗi: " + " | ".join(tail[-4:])[-400:])
    data = json.loads(out.read_text(encoding="utf-8"))
    sents = [s["text"] for s in data["sentences"]]
    sents = split_long_sentences(sents, [int(s["tokens"]) for s in data["sentences"]], settings=settings,
                                 language=data.get("language", "zh"), log=log, cancel=cancel)
    cues = sentence_cues(data["words"], sents)
    for p in (wav,):
        p.unlink(missing_ok=True)
    if on_progress:
        on_progress(1.0)
    if log:
        log(f"Qwen3-ASR: {len(cues)} câu" + (f" · sửa {data['repaired']} vùng căn mốc sụp" if data.get("repaired") else ""))
    return STTResult(cues=cues, language=data.get("language", ""), language_probability=1.0)
