"""Preset cho Story: độ dài, giọng văn, phong cách ảnh, preset kênh tham khảo (người dùng tự thêm)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config.paths import PRESETS_DIR

logger = logging.getLogger(__name__)

LENGTHS: dict[str, tuple[str, int, int]] = {
    # key: (nhãn, số ký tự mục tiêu, số cảnh gợi ý)
    "short": ("Ngắn (~2.500 ký tự · video ~2–3 phút)", 2500, 10),
    "medium": ("Vừa (~10.000 ký tự · video ~10–14 phút)", 10000, 32),
    "long": ("Dài (~25.000 ký tự · video ~30–35 phút)", 25000, 70),
    "xlong": ("Rất dài (~60.000 ký tự · video ~1–1.5 giờ)", 60000, 150),
}

TONES = [
    "Ấm áp, chữa lành, kể chuyện chậm rãi",
    "Bí ẩn, kịch tính sâu sắc (Gothic / Mystery)",
    "Hài hước, nhẹ nhàng, gần gũi",
    "Truyền cảm hứng, động lực",
    "Lãng mạn, cảm động",
    "Kinh dị, rùng rợn",
    "Tài liệu, giáo dục, khách quan",
    "Trẻ em, cổ tích, tươi sáng",
]

LANGUAGES = ["Tiếng Việt", "English", "中文", "日本語", "한국어", "Français", "Deutsch", "Español", "ไทย",
             "Bahasa Indonesia", "Русский"]

MASTER_PROMPTS: dict[str, tuple[str, str]] = {
    "cinematic_hyperrealism": (
        "Điện ảnh siêu thực",
        "Cinematic hyper-realistic photograph, shallow depth of field, dramatic volumetric lighting, film grain, "
        "8k, ultra detailed, moody atmosphere, 35mm lens"),
    "regency_romance": (
        "Cổ điển lãng mạn (Regency)",
        "Cinematic 19th-century period drama, aristocratic elegance, warm candlelight, grand manor interiors, "
        "soft volumetric lighting, nostalgic film grain, digital oil painting, 8k"),
    "drama_life": (
        "Đời sống ấm áp",
        "Warm golden hour lighting, cozy nostalgic atmosphere, gentle emotional mood, hyper-realistic cinematic "
        "portrait, soft bokeh, 8k photography"),
    "anime": (
        "Anime / hoạt hình",
        "High quality anime illustration, clean line art, vibrant colors, detailed background, studio quality, "
        "dynamic composition"),
    "watercolor": (
        "Tranh màu nước",
        "Soft watercolor painting, paper texture, gentle brush strokes, pastel palette, storybook illustration"),
    "dark_fantasy": (
        "Dark fantasy",
        "Dark fantasy concept art, epic scale, dramatic rim lighting, fog, intricate details, matte painting, "
        "trending on artstation"),
    "documentary": (
        "Tài liệu / chân thực",
        "Documentary style photograph, natural lighting, realistic, candid, high detail, neutral color grading"),
    "pixar3d": (
        "3D hoạt hình (Pixar-like)",
        "3D animated movie still, stylized characters, soft global illumination, expressive faces, vibrant, "
        "high quality render"),
}

DEFAULT_COMPETITORS: dict[str, dict] = {
    "Kể chuyện đêm khuya (mẫu)": {
        "name": "Kể chuyện đêm khuya (mẫu)",
        "genre_topic": "Chuyện đời, chiêm nghiệm, chữa lành cho người trung niên",
        "target_audience": "Người trên 40 tuổi, nghe trước khi ngủ",
        "language": "Tiếng Việt",
        "aspect_ratio": "16:9",
        "voice_provider": "Edge TTS",
        "voice_id": "vi-VN-NamMinhNeural",
        "voice_speed": 0.95,
        "master_prompt_preset": "drama_life",
        "master_prompt_custom": "",
        "character_lead_1": "Người đàn ông lớn tuổi từng trải, giọng kể ấm",
        "character_lead_2": "Người con xa quê trở về",
        "character_antagonist": "Những khó khăn của cuộc đời",
        "setting_location": "Làng quê Việt Nam, sân nhà buổi chiều, bến sông",
        "bgm_style": "Guitar acoustic nhẹ, piano ấm",
        "notes": "Video 15–30 phút, nhịp chậm, nhiều khoảng lặng, kết có bài học.",
    },
    "English mystery (sample)": {
        "name": "English mystery (sample)",
        "genre_topic": "Gothic mystery stories set in Victorian England",
        "target_audience": "Adults 25–55, US/UK, listen while commuting",
        "language": "English",
        "aspect_ratio": "16:9",
        "voice_provider": "Edge TTS",
        "voice_id": "en-GB-RyanNeural",
        "voice_speed": 0.95,
        "master_prompt_preset": "dark_fantasy",
        "master_prompt_custom": "",
        "character_lead_1": "A sharp, weary detective in a long coat",
        "character_lead_2": "A secretive housekeeper who knows too much",
        "character_antagonist": "A wealthy heir hiding a family crime",
        "setting_location": "Fog-covered London streets, candlelit manor, rainy train station",
        "bgm_style": "Slow strings, low piano, subtle tension",
        "notes": "Cliffhanger every 3–4 minutes, twist ending.",
    },
}


def competitors_file() -> Path:
    return PRESETS_DIR / "story_competitors.json"


def load_competitors() -> dict[str, dict]:
    p = competitors_file()
    if not p.exists():
        return dict(DEFAULT_COMPETITORS)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except Exception:
        logger.exception("story_competitors.json hỏng")
    return dict(DEFAULT_COMPETITORS)


def save_competitors(data: dict[str, dict]) -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    competitors_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def master_prompt_text(preset_key: str, custom: str) -> str:
    base = MASTER_PROMPTS.get(preset_key, ("", ""))[1]
    custom = (custom or "").strip()
    if custom and base:
        return f"{base}, {custom}"
    return custom or base


def competitor_brief(c: dict | None) -> str:
    """Tóm tắt preset kênh thành đoạn hướng dẫn cho AI."""
    if not c:
        return ""
    lines = []
    for label, key in (("Genre/topic", "genre_topic"), ("Target audience", "target_audience"),
                       ("Lead character 1", "character_lead_1"), ("Lead character 2", "character_lead_2"),
                       ("Antagonist / conflict", "character_antagonist"), ("Setting", "setting_location"),
                       ("Music style", "bgm_style"), ("Notes", "notes")):
        v = str(c.get(key, "")).strip()
        if v:
            lines.append(f"- {label}: {v}")
    return "\n".join(lines)
