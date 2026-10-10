"""VieNeu-TTS: giọng tiếng Việt chạy local (ONNX/CPU), có clone giọng từ file mẫu.

Gói ``vieneu`` là tuỳ chọn — cài bằng :func:`install_vieneu` (bỏ qua gradio/perth
không cần thiết). Model tải từ Hugging Face ở lần dùng đầu (vài trăm MB).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.config.paths import MODELS_DIR, ROOT
from app.services.tts.base import TTSEngine, Voice

logger = logging.getLogger(__name__)

CLONE_DIR = ROOT / "voices"           # người dùng bỏ file wav mẫu (5–15 giây) vào đây
_RUNTIME_DEPS = ["sea-g2p>=0.9.1", "onnxruntime>=1.20.0", "numpy", "soundfile", "soxr", "kaldi-native-fbank>=1.20",
                 "tokenizers>=0.20", "huggingface_hub", "PyYAML", "librosa>=0.11.0"]

_model = None
_model_lock = threading.Lock()
_last_used = 0.0


def is_installed() -> bool:
    try:
        import vieneu  # noqa: F401

        return True
    except Exception:
        return False


def install_vieneu(log: Callable[[str], None] | None = None) -> bool:
    """pip install vieneu (không kéo gradio). Chạy trong thread nền."""
    if getattr(sys, "frozen", False):
        if log:
            log("Bản đóng gói (.exe / .app) không cài thêm được gói Python — chạy bản mã nguồn (run.bat / run.command) để cài, hoặc dùng tính năng thay thế.")
        return False
    cmds = [
        [sys.executable, "-m", "pip", "install", "--upgrade", *_RUNTIME_DEPS],
        [sys.executable, "-m", "pip", "install", "--upgrade", "--no-deps", "vieneu"],
    ]
    for cmd in cmds:
        if log:
            log("pip: " + " ".join(cmd[3:]))
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if proc.returncode != 0:
            if log:
                log("pip lỗi: " + proc.stderr[-800:])
            return False
    return is_installed()


def release_idle(max_idle: float) -> bool:
    """Bỏ model VieNeu đã nạp (vài trăm MB RAM) nếu ``max_idle`` giây chưa dùng. Lần đọc sau nạp lại (~7 s)."""
    global _model
    with _model_lock:
        if _model is None or time.monotonic() - _last_used < max_idle:
            return False
        _model = None
    import gc

    gc.collect()
    logger.info("Đã giải phóng model VieNeu (lâu không dùng)")
    return True


def load_model(precision: str = "int8", log: Callable[[str], None] | None = None):
    global _model, _last_used
    with _model_lock:
        _last_used = time.monotonic()
        if _model is not None:
            return _model
        if not is_installed():
            if getattr(sys, "frozen", False):
                raise RuntimeError("Bản đóng gói (.exe / .app) không có VieNeu — chọn Edge TTS / ElevenLabs / Google, hoặc chạy bản mã nguồn (run.bat / run.command).")
            raise RuntimeError("Chưa cài VieNeu — bấm “Cài VieNeu” trong module Lồng tiếng.")
        os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
        from vieneu import Vieneu

        if log:
            log(f"Nạp VieNeu v3 Turbo ({precision}, ONNX/CPU) — lần đầu sẽ tải model…")
        _model = Vieneu(backend="onnx", precision=precision)
        logger.info("VieNeu sẵn sàng (sr=%s)", getattr(_model, "sample_rate", "?"))
        return _model


class VieNeuTTS(TTSEngine):
    name = "vieneu"
    ext = ".wav"
    native_speed_range = (1.0, 1.0)      # không chỉnh tốc độ gốc → dùng atempo
    workers = 1                          # CPU-bound
    # v3 Turbo (int8 lẫn fp32) hay đọc lặp / nói thêm / nuốt chữ đầu ở câu ngắn (~1/3 số lần đọc) — mỗi lần đọc
    # một kết quả khác → câu không đạt kiểm tra thì đọc lại (local, không tốn tiền)
    qc_retries = 3
    # đọc liền vài câu kề nhau cùng giọng (VieNeu tự gộp thành một khối) rồi tách lại: câu ngắn có ngữ cảnh — đo trên
    # 77 câu (audit_out/vieneu_q): câu ≤ 4 chữ Whisper nghe đúng 66% → 82% số lần đọc, câu dài 92% → 98%
    batch_lines = 6
    batch_chars = 160
    batch_adjacent = True

    def __init__(self, precision: str = "int8", log: Callable[[str], None] | None = None) -> None:
        self.precision = precision
        self.log = log
        self._voices: list[Voice] | None = None

    def cache_signature(self, voice_id: str, speed: float, pitch: int) -> str:
        return f"{self.name}|{self.precision}|{voice_id}"

    @staticmethod
    def _preset_voices_fast() -> list[tuple[str, str]]:
        """Đọc danh sách giọng mẫu từ file JSON kèm gói vieneu — tức thì, không phải nạp model (7 s, lần đầu tải 300 MB)."""
        import json

        import vieneu

        path = Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        presets = data.get("presets") or {}
        featured = sorted((k for k, v in presets.items() if v.get("featured")), key=lambda k: presets[k]["featured"])
        rest = sorted(k for k, v in presets.items() if not v.get("featured"))
        out = []
        for k in featured + rest:
            desc = presets[k].get("description", "")
            star = "⭐ " if presets[k].get("featured") else ""
            out.append((f"{star}{k} — {desc}" if desc else f"{star}{k}", k))
        return out

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        if self._voices is None:
            voices: list[Voice] = []
            try:
                presets = self._preset_voices_fast()
            except Exception as exc:                      # gói khác bản → hỏi model như trước
                logger.info("Không đọc được voices_v3_turbo.json (%s) — nạp model để lấy danh sách", exc)
                presets = list(load_model(self.precision, self.log).list_preset_voices())
            for label, vid in presets:
                gender = "Female" if "Nữ" in str(label) else ("Male" if "Nam" in str(label) else "")
                voices.append(Voice(id=str(vid), name=str(label), locale="vi-VN", gender=gender))
            CLONE_DIR.mkdir(parents=True, exist_ok=True)
            for wav in sorted(CLONE_DIR.glob("*.wav")) + sorted(CLONE_DIR.glob("*.mp3")):
                voices.append(Voice(id=f"ref:{wav}", name=f"🎤 Clone: {wav.stem}", locale="vi-VN", gender=""))
            self._voices = voices
        return list(self._voices)

    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        global _last_used
        import soundfile as sf

        model = load_model(self.precision, self.log)
        out_path = out_path.with_suffix(".wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with _model_lock:
            _last_used = time.monotonic()          # đang đọc cũng tính là đang dùng
            if voice_id.startswith("ref:"):
                audio = model.infer(text, ref_audio=voice_id[4:], denoise=True)
            else:
                audio = model.infer(text, voice=voice_id or None)
        sf.write(str(out_path), audio, int(getattr(model, "sample_rate", 48000)))
        return out_path

    def synthesize_batch(self, lines: list[str], voice_id: str) -> tuple[bytes, int]:
        global _last_used
        import numpy as np

        model = load_model(self.precision, self.log)
        # mỗi câu một dấu kết câu → VieNeu ngắt nghỉ giữa các câu (chỗ cắt khi tách lại)
        text = " ".join(t.strip() if t.strip()[-1:] in ".!?…" else t.strip().rstrip(",;:") + "." for t in lines)
        with _model_lock:
            _last_used = time.monotonic()
            if voice_id.startswith("ref:"):
                audio = model.infer(text, ref_audio=voice_id[4:], denoise=True)
            else:
                audio = model.infer(text, voice=voice_id or None)
        pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()
        return pcm, int(getattr(model, "sample_rate", 48000))
