"""Giao diện chung cho mọi engine TTS."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Voice:
    id: str
    name: str
    locale: str = ""
    gender: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        g = {"Female": "Nữ", "Male": "Nam", "female": "Nữ", "male": "Nam"}.get(self.gender, self.gender)
        return f"{self.name} · {g}" if g else self.name


class TTSEngine:
    """Mỗi engine cài ``list_voices`` và ``synthesize``; phần còn lại dùng chung."""

    name: str = "base"
    #: đuôi file âm thanh engine trả về (dùng cho cache)
    ext: str = ".mp3"
    #: khoảng tốc độ engine tự xử lý được; phần dư sẽ dùng atempo khi ghép
    native_speed_range: tuple[float, float] = (0.5, 2.0)
    #: số luồng gọi song song hợp lý
    workers: int = 4
    #: số lần tối đa được đọc lại một câu không đạt kiểm tra chất lượng (0 = không đọc lại: engine trả phí…)
    qc_retries: int = 0
    #: câu dài hơn thì tách đoạn đọc riêng rồi ghép (0 = engine tự chia câu dài)
    max_chars: int = 0
    #: đọc lại cả câu chỉ có lỗi nhẹ (vd «chữ đầu nghe thành…» — Whisper hay nghe nhầm). Engine tính tiền / lượt gọi
    #: đặt False: chỉ đọc lại khi lỗi nặng (đọc lặp, sai chữ, cụt…)
    reread_soft: bool = True
    #: đọc gộp tối đa bấy nhiêu câu trong một lượt gọi rồi tách lại (``synthesize_batch``; 0 = không gộp)
    batch_lines: int = 0

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        raise NotImplementedError

    def repair_cached(self, path: Path) -> None:
        """Sửa file cache do bản cũ ghi sai định dạng (mặc định: không làm gì)."""

    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        raise NotImplementedError

    def synthesize_batch(self, lines: list[str], voice_id: str) -> tuple[bytes, int]:
        """Đọc liền nhiều câu trong một lượt gọi (nghỉ rõ giữa các câu) → (PCM s16 mono, tần số)."""
        raise NotImplementedError

    def cache_signature(self, voice_id: str, speed: float, pitch: int) -> str:
        """Chuỗi tham số ảnh hưởng tới âm thanh — dùng làm khoá cache."""
        return f"{self.name}|{voice_id}|{self.clamp_speed(speed):.3f}|{pitch}"

    def clamp_speed(self, speed: float) -> float:
        lo, hi = self.native_speed_range
        return max(lo, min(hi, speed))

    def residual_tempo(self, speed: float) -> float:
        """Phần tốc độ engine không làm được, ghép sẽ bù bằng atempo."""
        native = self.clamp_speed(speed)
        return speed / native if native else 1.0


def part_path(out_path: Path) -> Path:
    """File tạm riêng cho từng luồng/lần gọi: hai cue cùng nội dung (cùng cache key) chạy song song không ghi đè
    chung một ``.part`` (WinError 32 «file đang được dùng»)."""
    import os
    import threading

    return out_path.with_name(f"{out_path.stem}.{os.getpid()}-{threading.get_ident()}.part")
