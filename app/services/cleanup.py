"""Dọn dẹp file đang làm: phụ đề, bản dịch, giọng lồng, job tạm, cache xem trước / TTS / khử giọng.

Giữ lại: video đã xuất, dự án Dựng truyện, mẫu, cài đặt + API key, model AI, bộ nhớ dịch (đỡ tốn lượt Gemini).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config.paths import CACHE_DIR, JOBS_DIR, PREVIEW_CACHE_DIR, SUBS_DIR, TTS_CACHE_DIR, VOICE_DIR

#: (nhóm hiển thị, thư mục)
WORK_DIRS: tuple[tuple[str, Path], ...] = (
    ("work", SUBS_DIR), ("work", VOICE_DIR),
    ("temp", JOBS_DIR), ("temp", PREVIEW_CACHE_DIR), ("temp", CACHE_DIR / "speaker_clips"),
    ("cache", TTS_CACHE_DIR), ("cache", CACHE_DIR / "demucs"), ("cache", CACHE_DIR / "novocal"),
)


def _size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def sizes() -> dict[str, float]:
    """MB theo nhóm: work (phụ đề + giọng) · temp (job + xem trước) · cache (TTS, khử giọng)."""
    out = {"work": 0.0, "temp": 0.0, "cache": 0.0}
    for group, d in WORK_DIRS:
        out[group] += _size(d) / 1_048_576
    return out


def _protected(d: Path, keep: list[Path]) -> bool:
    """Thư mục xuất nằm trong ``d`` (người dùng đặt Thư mục xuất vào output/voice…) → không xoá."""
    try:
        d = d.resolve()
    except OSError:
        return True
    return any(k == d or d in k.parents for k in keep)


def clean(keep: list[str | Path] = ()) -> tuple[float, int]:
    """Xoá file đang làm. Trả (MB đã giải phóng, số file không xoá được — đang mở ở chương trình khác)."""
    keep_paths = []
    for k in keep:
        try:
            keep_paths.append(Path(k).resolve())
        except OSError:
            pass
    freed, stuck = 0, 0
    for _group, d in WORK_DIRS:
        if not d.exists() or _protected(d, keep_paths):
            continue
        before = _size(d)
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        stuck += sum(1 for f in d.rglob("*") if f.is_file())
        freed += before - _size(d)
    return freed / 1_048_576, stuck
