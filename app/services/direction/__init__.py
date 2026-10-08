"""Đạo diễn âm thanh: bánh xe cảm xúc + kiểu nói → lời dặn cách đọc cho engine TTS.

Module thuần (không gọi API, không UI): dữ liệu cảm xúc / kiểu nói trong ``data/``, thuật toán pha trộn và biên dịch
sang ``style_en`` (câu lời dặn cho Gemini) + tham số cho Edge (rate/pitch) + mô tả tiếng Việt để hiển thị.
"""

from app.services.direction.blend import (
    Compiled,
    blend,
    compile_direction,
    emotions,
    emotions_map,
    speaking_styles,
)

__all__ = ["Compiled", "blend", "compile_direction", "emotions", "emotions_map", "speaking_styles"]
