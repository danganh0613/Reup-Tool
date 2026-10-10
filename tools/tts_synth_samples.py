"""Đọc bộ câu test (tests/tts_samples.txt) bằng đúng engine + cấu hình + pipeline của app, NGOÀI tool đang chạy.

    python tools/tts_synth_samples.py --out audit_out/after               # VieNeu + Gemini
    python tools/tts_synth_samples.py --engines vieneu --takes 3 --out audit_out/after
    python tools/tts_synth_samples.py --stress "Con này bắt về nuôi được không?" --stress-takes 10 --out audit_out/after

Đi qua ``synthesize_cached`` (chuẩn hoá chữ → đọc → kiểm chất lượng / đọc lại → làm sạch) với cache RIÊNG trong
thư mục --out (không đụng cache/tts hay output/ của app). Mỗi câu có 2 bản:
  · *_raw.wav  — bản engine được giữ lại trong cache (đã chọn qua kiểm tra chất lượng, đã làm sạch)
  · *_tool.wav — sau đúng các bước app làm khi ghép track (giải mã 24 kHz + atempo theo «Tốc độ» + hậu kỳ từng câu)
Bản «trước khi sửa» (audit_out/before) được tạo bằng code cũ (commit c34d10f).
Key Gemini: biến môi trường GEMINI_API_KEY / GOOGLE_API_KEY → file .env ở thư mục gốc → key đã nhập trong app.
Không in key ra màn hình / log.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def env_keys() -> list[str]:
    keys = [os.environ.get(k, "") for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v.strip():
                keys.append(v.strip().strip('"').strip("'"))
    return [k for k in dict.fromkeys(keys) if k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text-file", default=str(ROOT / "tests" / "tts_samples.txt"))
    ap.add_argument("--engines", default="vieneu,gemini")
    ap.add_argument("--takes", type=int, default=2, help="số lần đọc mỗi câu với VieNeu (mỗi lần cache riêng)")
    ap.add_argument("--gemini-styles", default="Tự nhiên,Review phim cuốn hút",
                    help="phong cách Gemini để thử (từ phong cách thứ 2 chỉ đọc câu 1 và 4)")
    ap.add_argument("--stress", default="", help="một câu đọc lặp nhiều lần để đo tỉ lệ lỗi")
    ap.add_argument("--stress-takes", type=int, default=10)
    ap.add_argument("--no-qc", action="store_true", help="tắt kiểm chất lượng (chỉ chuẩn hoá + làm sạch)")
    ap.add_argument("--out", default=str(ROOT / "audit_out" / "after"))
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from app.core.state import AppState
    from app.services.ffmpeg import FFmpeg
    from app.services.tts import generator, quality

    st = AppState()
    st.load_settings()
    cfg_path = ROOT / "output" / "last_config.json"
    tts = json.loads(cfg_path.read_text(encoding="utf-8")).get("tts", {}) if cfg_path.exists() else {}
    speed = float(tts.get("tts_speed") or 1.0)
    ff = FFmpeg()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = [ln.strip() for ln in Path(a.text_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    samples: list[dict] = []
    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    qc = None if a.no_qc else generator.qc_options(st.settings)
    polish = generator.polisher(st.settings)
    log = open(out / "synth_log.txt", "a", encoding="utf-8")

    def note(msg: str) -> None:
        print(msg, flush=True)
        log.write(time.strftime("%H:%M:%S ") + msg + "\n")
        log.flush()

    def run(eng, voice: str, text: str, stem: str, cache: Path, **meta) -> None:
        generator.TTS_CACHE_DIR = cache                    # cache riêng của lần chạy thử
        t0 = time.perf_counter()
        try:
            p = generator.synthesize_cached(eng, voice, text, speed=speed, pitch=0, qc=qc)
        except Exception as exc:
            note(f"  {stem}: LỖI {str(exc)[:200]}")
            return
        dt = time.perf_counter() - t0
        raw = out / f"{stem}_raw.wav"
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, raw)
        pcm = polish(ff.decode_pcm(p, sample_rate=generator.SAMPLE_RATE, tempo=eng.residual_tempo(speed)))
        from app.services.tts.postprocess import write_wav

        write_wav(out / f"{stem}_tool.wav", pcm, generator.SAMPLE_RATE)
        m = quality.read_mark(p) or {}
        note(f"  {stem}: {dt:.1f}s · đọc {m.get('tries', 1)} lần · điểm {m.get('score', 0)}"
             + (f" ({'; '.join(m.get('problems') or [])})" if m.get("problems") else "") + f" · {text}")
        samples.append({"file": str(raw), "text": text, "variant": "raw", "tries": m.get("tries", 1), **meta})
        samples.append({"file": str(out / f"{stem}_tool.wav"), "text": text, "variant": "tool", **meta})

    note(f"Pipeline mới · kiểm chất lượng: {'tắt' if qc is None else f'bật, đọc lại tối đa {qc.retries} lần' + (' + Whisper' if qc.asr else '')}")
    if "vieneu" in engines:
        from app.services.tts.vieneu import VieNeuTTS, load_model

        prec = st.settings.vieneu_precision or "int8"
        voice = tts.get("vieneu_voice") or ""
        eng = VieNeuTTS(precision=prec)
        t0 = time.perf_counter()
        load_model(prec)
        note(f"VieNeu v3 Turbo · {prec} · giọng «{voice}» · nạp {time.perf_counter() - t0:.1f}s · tốc độ app ×{speed}")
        for i, text in enumerate(lines):
            for k in range(a.takes):
                run(eng, voice, text, f"vieneu_s{i + 1}_t{k + 1}", out / "cache" / f"t{k + 1}", engine="vieneu")
        for k in range(a.stress_takes if a.stress else 0):
            run(eng, voice, a.stress, f"stress/vieneu_x{k + 1:02d}", out / "cache" / f"x{k + 1:02d}", engine="vieneu")

    if "gemini" in engines:
        from app.services.tts.gemini_tts import GeminiTTS

        keys = env_keys()
        src = "biến môi trường/.env"
        if not keys:
            keys, src = st.settings.gemini_keys(), "key đã lưu trong app (Cài đặt)"
        model = tts.get("gemini_tts_model") or ""
        voice = tts.get("gemini_voice") or "Leda"
        temp = st.settings.gemini_tts_temperature
        note(f"Gemini TTS · model {model} · giọng {voice} · temperature {temp} · {len(keys)} key ({src})")
        for si, style in enumerate(s.strip() for s in a.gemini_styles.split(",") if s.strip()):
            eng = GeminiTTS(keys, model=model, style=style, custom_style=tts.get("gemini_style_custom") or "",
                            temperature=temp if temp > 0 else None)
            for i, text in enumerate(lines):
                if si > 0 and i not in (0, 3):
                    continue
                run(eng, voice, text, f"gemini_s{i + 1}_{'tn' if si == 0 else f'st{si}'}", out / "cache" / "gemini",
                    engine="gemini", style=style)

    # gộp với các lần chạy trước (chạy riêng từng engine)
    sj = out / "samples.json"
    old = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else []
    new_files = {s["file"] for s in samples}
    merged = [s for s in old if s["file"] not in new_files] + samples
    sj.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    note(f"Xong: {len(samples)} file mới · tổng {len(merged)} → {sj}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
