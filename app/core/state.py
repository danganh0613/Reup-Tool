"""Trạng thái ứng dụng: cấu hình từng module, danh sách video, cài đặt máy.

- ``AppState`` giữ mọi thứ UI cần; module UI chỉ đọc/ghi vào đây.
- Autosave: mỗi lần ``mark_dirty`` sẽ ghi ``last_config.json`` sau một nhịp
  ngắn (gộp nhiều thay đổi liên tiếp thành một lần ghi).
- Preset: cùng cấu trúc JSON, lưu trong thư mục ``presets``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from app.config.paths import CONFIG_FILE, SETTINGS_FILE, VIDEOS_DIR
from app.services.gemini_models import migrate_model

logger = logging.getLogger(__name__)

# ── Hằng lựa chọn (đồng thời là nhãn hiển thị) ────────────────────────────
RATIOS = ["Giữ nguyên", "21:9 (Điện ảnh)", "16:9 (Ngang)", "4:3", "1:1 (Vuông)", "3:4", "4:5 (Dọc ngắn)", "9:16 (Dọc)"]
RATIO_VALUES: dict[str, float | None] = {"Giữ nguyên": None, "21:9 (Điện ảnh)": 21 / 9, "16:9 (Ngang)": 16 / 9, "4:3": 4 / 3,
                                         "1:1 (Vuông)": 1.0, "3:4": 3 / 4, "4:5 (Dọc ngắn)": 4 / 5, "9:16 (Dọc)": 9 / 16}
FIT_MODES = ["Cắt vừa khung", "Thêm viền đen", "Nền mờ (blur)"]
RESOLUTIONS = ["Giữ nguyên", "720p", "1080p", "1440p", "4K"]
FPS_OPTIONS = ["Giữ nguyên", "24", "25", "30", "50", "60"]
# macOS chỉ có VideoToolbox; cấu hình mang từ máy Windows sang (nvenc…) vẫn chạy: không dùng được thì tự dò tiếp rồi về CPU
GPU_BACKENDS = ["auto", "videotoolbox", "cpu"] if sys.platform == "darwin" else ["auto", "nvenc", "qsv", "amf", "cpu"]
VIDEO_CODECS = ["H.264 / AVC", "H.265 / HEVC", "AV1"]
ENCODE_PRESETS = ["Nhanh", "Cân bằng", "Chất lượng"]

#: phông phụ đề mặc định kiểu TikTok: chữ đậm đủ dấu tiếng Việt (Arial Black / Impact thiếu dấu) — Segoe UI Black có sẵn
#: trên Windows (đã là nét đậm, không bật «Đậm» kẻo libass tô đậm thêm); máy khác dùng Arial đậm
SUB_FONT_DEFAULT, SUB_BOLD_DEFAULT = ("Segoe UI Black", False) if sys.platform == "win32" else ("Arial", True)
SRT_SOURCES = ["Tự nhận diện (Whisper)", "Chọn file SRT"]
#: (cũ) cách lấy lời thoại — giờ máy tự chọn (nghe giọng nói + đối chiếu chữ in sẵn), giữ trường để đọc cấu hình cũ
#: (Douyin…) — đúng từng chữ, kể cả tên riêng
STT_ENGINES = ["asr", "ocr"]
STT_LABELS = {"asr": "Giọng nói trong video", "ocr": "Chữ in sẵn trên video"}
TEXT_EFFECTS = ["Không có (Khuyên dùng)", "Viền đậm", "Bóng đổ", "Viền + bóng", "Phát sáng (glow)"]
SUB_BG_STYLES = ["Không nền", "Hộp mờ bo góc", "Hộp đen", "Dải ngang toàn khung", "Bóng mềm"]
TRIM_MODES = ["Ghép các đoạn thành 1 video", "Xuất mỗi đoạn thành 1 file"]
COLOR_PRESETS = ["Mặc định", "Ấm áp", "Điện ảnh", "Tươi sáng", "Cổ điển", "Lạnh"]
STT_LANGUAGES = ["auto", "vi", "en", "zh", "ja", "ko", "fr", "de", "es", "th", "id", "ru"]
#: «Ngôn ngữ video» (mã) → tên ngôn ngữ gốc đưa cho bước dịch — trước đây là hai ô riêng ở hai tab
SOURCE_BY_CODE = {"auto": "Tự nhận diện", "vi": "Tiếng Việt", "en": "English", "zh": "中文", "ja": "日本語", "ko": "한국어",
                  "fr": "Français", "de": "Deutsch", "es": "Español", "th": "ไทย", "id": "Bahasa Indonesia",
                  "ru": "Русский"}
SRT_MODES = ["Sub gốc", "Sub đã dịch", "Song ngữ (dịch trên, gốc dưới)"]
SRT_MODE_LABELS = {SRT_MODES[0]: "Lời gốc", SRT_MODES[1]: "Bản dịch", SRT_MODES[2]: "Song ngữ (dịch trên, gốc dưới)"}
SUB_POSITIONS = ["Dưới", "Giữa", "Trên", "Tuỳ chỉnh (X/Y)"]
TRANSLATE_TARGETS = ["Tiếng Việt", "English", "中文", "日本語", "한국어", "Français", "Deutsch",
                     "Español", "ไทย", "Bahasa Indonesia", "Русский"]

TEXTURE_BLENDS = ["screen", "overlay", "softlight", "multiply", "lighten", "addition"]
BLUR_STYLES = ["Mờ (Gaussian)", "Ô vuông (pixelate)", "Che kín (màu đen)"]
MOTIONS = ["Đứng yên", "Trôi ngang", "Trôi dọc", "Nảy (bounce)", "Đường chéo", "Nhấp nháy"]
POSITIONS = ["Trên trái", "Trên giữa", "Trên phải", "Giữa trái", "Chính giữa", "Giữa phải",
             "Dưới trái", "Dưới giữa", "Dưới phải"]
SAFE_ZONES = ["Toàn khung", "TikTok/Reels (né UI phải & dưới)"]
KENBURNS = ["Không", "Zoom vào", "Zoom ra", "Trái → phải", "Phải → trái", "Trên → dưới"]

VIENEU = "VieNeu (local, tiếng Việt)"
#: VieNeu (chạy trên máy, miễn phí) mặc định · Edge TTS (cần mạng) — cũng là giọng dự phòng khi VieNeu đọc lỗi
TTS_PROVIDERS = [VIENEU, "Edge TTS"]
TTS_LABELS = {VIENEU: "VieNeu — giọng Việt, chạy trên máy", "Edge TTS": "Edge TTS — cần mạng, nhiều thứ tiếng"}
#: engine đã gỡ — cấu hình cũ còn ghi thì tự chuyển sang VieNeu
RETIRED_TTS = ("Gemini TTS (Google AI)", "Gemini Live (không giới hạn lượt)", "ElevenLabs", "Google Cloud TTS", "FPT.AI")


def provider_or_default(provider: str) -> str:
    """Engine đã gỡ (nhân vật Dựng truyện, mẫu cũ…) → VieNeu."""
    return provider if provider in TTS_PROVIDERS else VIENEU


#: giọng VieNeu thay cho giọng Gemini cũ theo giới tính
VIENEU_BY_GENDER = {"Female": "Trúc Ly", "Male": "Thanh Bình"}
TTS_SOURCES = ["Sub đã dịch", "Sub gốc"]
#: tên hiển thị (giá trị lưu giữ nguyên cho mẫu / cấu hình cũ) — cùng cách gọi «Lời gốc / Bản dịch» ở mọi tab
TTS_SOURCE_LABELS = {TTS_SOURCES[0]: "Bản dịch", TTS_SOURCES[1]: "Lời gốc"}
SPEAKER_COUNTS = ["Tự động", "1", "2", "3", "4", "5", "6", "8"]


# ── Dataclass cấu hình ───────────────────────────────────────────────────

@dataclass
class VideoConfig:
    ratio: str = RATIOS[0]
    fit_mode: str = FIT_MODES[0]
    resolution: str = RESOLUTIONS[0]
    fps: str = FPS_OPTIONS[0]
    zoom: int = 100
    speed: int = 100
    flip_h: bool = False
    mute: bool = False
    keep_original_audio: bool = True
    vocal_filter: bool = False
    use_gpu: bool = True
    gpu_backend: str = "auto"
    video_codec: str = VIDEO_CODECS[0]
    quality: int = 23
    encode_preset: str = ENCODE_PRESETS[1]
    original_volume: float = 100.0
    tts_volume: float = 140.0
    adaptive_ducking: bool = True
    normalize_audio: bool = True
    output_suffix: str = "_edit"
    output_name: str = ""              # mẫu tên file: {ten} {hauto} {mau} {ngay} {gio} {stt} — trống = {ten}{hauto}
    output_subdir: str = ""            # thư mục con trong thư mục xuất (nhận cùng các mã trên)
    loop_to_voice: bool = False        # lặp video cho đủ thời lượng giọng đọc
    gop_size: int = 0                  # khoảng keyframe (0 = mặc định encoder)
    demucs_enable: bool = False        # khử giọng nói gốc bằng AI (MDX-Net ONNX), giữ nhạc nền + tiếng động

    def __post_init__(self) -> None:
        if self.vocal_filter:              # cấu hình cũ: «lọc vocal» (L−R, kém) → chuyển sang khử giọng AI
            self.vocal_filter = False
            self.demucs_enable = True


@dataclass
class SubtitleConfig:
    enable_subtitle: bool = True
    srt_source: str = SRT_SOURCES[0]
    stt_language: str = "auto"
    translate_enable: bool = False
    translate_target: str = TRANSLATE_TARGETS[0]
    translate_style: str = ""          # ghi chú phong cách dịch (tuỳ chọn)
    srt_mode: str = SRT_MODES[0]
    # mặc định kiểu phụ đề video viral TikTok (xem services/subtitle_presets.py): chữ đậm trắng viền đen dày + bóng nhẹ
    sub_font: str = SUB_FONT_DEFAULT
    sub_size: int = 66
    sub_bold: bool = SUB_BOLD_DEFAULT
    sub_italic: bool = False
    sub_uppercase: bool = False
    sub_color: str = "#FFFFFF"
    sub_opacity: float = 100.0
    sub_stroke_width: float = 5.0
    sub_stroke_color: str = "#000000"
    sub_shadow: float = 2.0
    sub_bg_enable: bool = False
    sub_bg_color: str = "#000000"
    sub_bg_opacity: float = 60.0
    sub_letter_spacing: float = 0.0
    sub_position: str = SUB_POSITIONS[0]
    sub_margin_v: int = 60
    sub_max_width: int = 90            # % chiều rộng khung
    sub_x: int = 50                    # % (chế độ Tuỳ chỉnh)
    sub_y: int = 85                    # %
    sub_delay: float = 0.0             # giây, âm = sớm hơn
    secondary_scale: float = 0.8       # cỡ dòng phụ khi Song ngữ
    # Khung phụ đề (tỉ lệ 0..1 theo khung ra): chữ tự xuống dòng theo bề rộng khung, căn đáy khung — đáy ở 76% chiều
    # cao: trên vùng tên kênh / chú thích / nút của TikTok · Reels · Shorts
    use_box: bool = True
    box_x: float = 0.08
    box_y: float = 0.68
    box_w: float = 0.84
    box_h: float = 0.08
    follow_hardsub: bool = False       # đặt khung lên đúng chỗ chữ cứng đã dò & che (mỗi video một chỗ)
    #: khối chữ căn giữa khung theo chiều dọc (máy tự đặt khi bám chỗ chữ cứng — câu 1 hay 2 dòng đều nằm giữa vùng
    #: đã che); False = chữ bám đáy khung
    box_center: bool = False
    text_effect: str = "Không có (Khuyên dùng)"
    bg_style: str = "Không nền"
    karaoke: bool = False              # chữ đang đọc nổi lên (kiểu ở ``karaoke_mode``)
    karaoke_color: str = "#FFD400"
    #: pop = chữ đang đọc đổi màu + phóng nhẹ rồi trở lại (kiểu CapCut) · fill = tô màu dần (giữ màu) ·
    #: reveal = chữ hiện dần đúng lúc được đọc
    karaoke_mode: str = "pop"
    stt_engine: str = STT_ENGINES[0]
    translate_source: str = "Tự nhận diện"          # cấu hình cũ; nay theo «Ngôn ngữ video» (stt_language)

    def migrate(self) -> None:
        """Cấu hình cũ (Whisper / Gemini / SenseVoice / Qwen3-ASR ghi bằng tên dài) → «asr»; OCR → «ocr»."""
        if self.stt_engine not in STT_ENGINES:
            self.stt_engine = "ocr" if self.stt_engine.upper().startswith("OCR") else "asr"

    @property
    def source_hint(self) -> str:
        """Ngôn ngữ gốc cho bước dịch = «Ngôn ngữ video» ở tab Nhận diện (ô «Ngôn ngữ gốc» cũ đã bỏ khỏi giao diện —
        giá trị cũ còn trong mẫu không được âm thầm ép ngôn ngữ nữa)."""
        return SOURCE_BY_CODE.get(self.stt_language or "auto", "Tự nhận diện")


@dataclass
class TTSConfig:
    enable_tts: bool = False
    tts_provider: str = TTS_PROVIDERS[0]
    tts_source: str = TTS_SOURCES[0]
    edge_language: str = "vi-VN"
    edge_voice: str = "vi-VN-HoaiMyNeural"
    vieneu_voice: str = "Minh Quân Pro"
    tts_speed: float = 1.0
    tts_pitch: int = 0                 # Hz, Edge only
    #: khớp mềm (luôn bật): dời câu vào khoảng lặng, nén từ 1,10× tăng dần tới trần này, câu bị đẩy trễ đọc nhanh hơn
    #: để đuổi kịp hình (đo: «đọc tự nhiên không nén» làm giọng trễ dồn tới 4,8 s với bản dịch câu dài)
    max_tempo: float = 1.5
    min_gap: float = 0.08              # khoảng nghỉ tối thiểu giữa 2 cue (giây)
    multi_voice: bool = True           # mỗi người nói một giọng (tự nhận diện); muốn một người đọc hết thì tắt
    speaker_count: str = SPEAKER_COUNTS[0]
    #: giọng theo người nói: {name, provider, locale, voice_id, voice_name, speed}
    speaker_voices: list = field(default_factory=list)

    def set_voice(self, voice_id: str) -> None:
        """Ghi cùng một mã giọng cho mọi nhà cung cấp (dùng khi đổi giọng theo nhân vật / người nói)."""
        self.edge_voice = self.vieneu_voice = voice_id

    def migrate(self, data: dict | None = None) -> bool:
        """Cấu hình cũ dùng engine đã gỡ (Gemini TTS / Live, ElevenLabs, Google, FPT.AI) → VieNeu: giọng Gemini cũ đổi
        theo giới tính (``data``: dict gốc — trường ``gemini_voice`` không còn trong TTSConfig), giọng trả phí của
        người nói thì để trống cho máy tự gợi ý lại. True nếu có đổi."""
        changed = False
        if self.tts_provider in RETIRED_TTS:
            if self.tts_provider.startswith("Gemini"):
                self.vieneu_voice = vieneu_for_gemini(str((data or {}).get("gemini_voice") or ""))
            self.tts_provider = VIENEU
            changed = True
        for sv in self.speaker_voices:
            if isinstance(sv, dict) and sv.get("provider") in RETIRED_TTS:
                v = vieneu_for_gemini(str(sv.get("voice_id") or "")) if str(sv["provider"]).startswith("Gemini") else ""
                sv.update(provider=VIENEU, locale="vi-VN", voice_id=v, voice_name=v)
                changed = True
        return changed


#: giọng nam của Gemini TTS cũ (engine đã gỡ) — để chuyển cấu hình cũ sang giọng VieNeu cùng giới tính
_GEMINI_MALE = {"Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Algenib", "Rasalgethi",
                "Alnilam", "Schedar", "Achird", "Zubenelgenubi", "Sadachbia", "Sadaltager"}


def vieneu_for_gemini(voice: str) -> str:
    return VIENEU_BY_GENDER["Male" if voice in _GEMINI_MALE else "Female"]


@dataclass
class BlurConfig:
    enable: bool = False
    #: mỗi vùng: {x, y, w, h (0..1 theo khung video GỐC), strength, feather, opacity, style, start, end}
    regions: list = field(default_factory=list)
    active_index: int = 0
    auto_detect: bool = False          # tự dò vùng chữ cứng của từng video và che thêm vùng đó


@dataclass
class BGMConfig:
    enable: bool = False
    #: mỗi clip: {path, volume, start, offset, loop, fade_in, fade_out}
    clips: list = field(default_factory=list)
    #: hệ số chung cũ (ẩn, 20 %) — nay luôn 100: âm lượng từng đoạn là mức thật (``_fold_bgm_master``)
    master_volume: float = 100.0
    ducking: bool = True
    loop: bool = True
    fade_in: float = 1.5
    fade_out: float = 2.5


@dataclass
class BrandConfig:
    logo_enable: bool = False
    logo_path: str = ""
    logo_size: float = 16.0            # % chiều rộng khung
    logo_opacity: float = 100.0
    logo_motion: str = MOTIONS[0]
    logo_position: str = POSITIONS[2]
    logo_motion_speed: float = 40.0    # px/giây
    logo_delay: float = 0.0
    text_enable: bool = False
    text: str = ""
    text_font: str = "Arial"
    text_size: int = 40
    text_color: str = "#FFFFFF"
    text_opacity: float = 100.0
    text_bg: bool = False
    text_motion: str = MOTIONS[0]
    text_position: str = POSITIONS[1]
    text_motion_speed: float = 40.0
    text_delay: float = 0.0
    watermark_enable: bool = False
    watermark_text: str = ""
    watermark_font: str = "Arial"
    watermark_size: int = 30
    watermark_color: str = "#FFFFFF"
    watermark_opacity: float = 45.0
    watermark_motion: str = MOTIONS[0]
    watermark_position: str = POSITIONS[4]
    watermark_motion_speed: float = 35.0
    watermark_delay: float = 0.0
    safe_margin: int = 32
    safe_zone: str = SAFE_ZONES[0]
    motion_cycle: float = 8.0          # giây / chu kỳ chuyển động
    frame_enable: bool = False         # khung viền trang trí
    frame_width: int = 12
    frame_color: str = "#FFFFFF"
    frame_opacity: float = 100.0


@dataclass
class TrimConfig:
    enable: bool = False
    head: str = "0"
    tail: str = "0"
    #: mỗi đoạn: {start, end, name} (chuỗi HH:MM:SS hoặc giây; end rỗng = hết video)
    segments: list = field(default_factory=list)
    mode: str = "Ghép các đoạn thành 1 video"


@dataclass
class ColorConfig:
    enable: bool = False
    rotate: float = 0.0
    brightness: float = 0.0            # -1..1
    contrast: float = 1.0              # 0..2
    saturation: float = 1.0            # 0..3
    gamma: float = 1.0                 # 0.3..3
    temperature: float = 0.0           # -100 (lạnh) .. 100 (ấm)
    sharpen: float = 0.0               # 0..2
    grain: float = 0.0                 # 0..100
    vignette: float = 0.0              # 0..100
    kenburns: str = KENBURNS[0]
    kenburns_amount: float = 8.0       # %
    texture_enable: bool = False
    texture_path: str = ""             # video/ảnh texture (film grain, dust, light leak…)
    texture_blend: str = "screen"      # screen | overlay | softlight | multiply | lighten
    texture_opacity: float = 35.0
    preset: str = "Mặc định"
    deshake: bool = False
    # Chống trùng (biến thể nhẹ cho video tự đăng lại trên nhiều kênh)
    dup_shift: bool = False            # dịch khung vài pixel
    dup_noise: bool = False            # nhiễu động nhẹ theo thời gian
    dup_color: bool = False            # lệch màu nhẹ
    dup_zoompan: bool = False          # phóng 2% + trượt chậm
    dup_audio: bool = False            # lệch cao độ âm ~2% (giữ nhịp, không lệch sync)
    dup_gop: bool = False              # đổi khoảng keyframe (GOP)


STORY_RATIOS = ["16:9 (YouTube ngang)", "9:16 (Dọc)", "1:1 (Vuông)"]
STORY_RESOLUTIONS = ["720p", "1080p", "4K"]


@dataclass
class StoryConfig:
    text_model: str = "gemini-flash-latest"
    image_model: str = "gemini-3.1-flash-image"
    video_model: str = "veo-3.1-fast-generate-preview"
    video_duration: int = 8
    use_ai_video: bool = False
    ratio: str = STORY_RATIOS[0]
    resolution: str = STORY_RESOLUTIONS[1]
    provider: str = VIENEU                 # giọng Việt chạy trên máy, không cần mạng (như tab TTS)
    voice_language: str = "vi-VN"
    voice_id: str = VIENEU_BY_GENDER["Female"]
    voice_name: str = ""
    speed: float = 1.0
    gap: float = 0.4
    kenburns_amount: float = 8.0
    language: str = "Tiếng Việt"
    length: str = "medium"
    tone: str = "Ấm áp, chữa lành, kể chuyện chậm rãi"
    genre: str = ""
    competitor: str = ""
    master_prompt_preset: str = "cinematic_hyperrealism"
    master_prompt_custom: str = ""
    last_project_path: str = ""
    auto_queue: bool = True


@dataclass
class Settings:
    """Cài đặt gắn với máy — lưu riêng ở ``settings.json``."""
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    appearance_mode: str = "dark"      # dark | oled
    accent: str = "lime"
    theme: str = "studio"             # studio · midnight · graphite · orchid · oled
    output_dir: str = ""
    windows_toast: bool = True
    vieneu_precision: str = "int8"     # int8 (nhanh) | fp32 (chất lượng)
    #: model dùng để dịch / tách câu / sửa phụ đề (Gemini hoặc DeepSeek «deepseek-…») — hết lượt hay quá tải thì dừng
    #: và báo, không tự đổi model
    translate_model: str = "gemini-flash-latest"
    gemini_api_keys: str = ""          # nhiều key, mỗi dòng một key (xoay vòng khi hết quota)
    deepseek_api_key: str = ""         # DeepSeek (trả phí theo lượng dùng) — dùng khi model dịch là «deepseek-…»
    glossary: str = ""                 # mỗi dòng: từ nguồn=nghĩa

    @staticmethod
    def split_keys(text: str) -> list[str]:
        """Ô key nhiều dòng → danh sách key (mỗi dòng / dấu phẩy một key, bỏ trùng, giữ thứ tự)."""
        raw = (text or "").replace(",", "\n").replace(";", "\n")
        return list(dict.fromkeys(k.strip() for k in raw.splitlines() if k.strip()))

    def gemini_keys(self) -> list[str]:
        return self.split_keys(self.gemini_api_keys)

    def uses_deepseek(self) -> bool:
        return (self.translate_model or "").strip().lower().startswith("deepseek")

    def translate_provider(self) -> str:
        return "DeepSeek" if self.uses_deepseek() else "Gemini"

    def translate_keys(self) -> list[str]:
        """Key của nhà cung cấp model dịch đang chọn (Gemini / DeepSeek)."""
        return self.split_keys(self.deepseek_api_key) if self.uses_deepseek() else self.gemini_keys()
    show_waveform: bool = True
    show_guide_on_start: bool = True     # mở Hướng dẫn ở lần chạy đầu (tắt trong cửa sổ hướng dẫn)
    # hậu kỳ từng câu lồng tiếng (kiểm lỗi + đọc lại, đọc liền, AI rút gọn câu tràn, dự phòng Edge: luôn bật)
    tts_fade_in_ms: int = 10             # fade đầu / cuối mỗi câu — hết «tách» chỗ nối
    tts_fade_out_ms: int = 25
    tts_level_db: float = -18.0          # đưa mọi câu về cùng độ to (dBFS phần có tiếng); 0 = giữ nguyên
    loudness_target: float = -14.0       # độ to file xuất (LUFS) khi bật «Chuẩn hoá âm lượng»: TikTok/FB/YouTube −14


@dataclass
class VideoItem:
    path: str
    srt_path: str | None = None            # SRT gốc (nhận diện hoặc người dùng chọn)
    srt_translated_path: str | None = None
    voice_path: str | None = None          # track TTS đã ghép
    status: str = "Chờ"
    progress: float = 0.0
    message: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = True
    output_path: str | None = None
    template: str = ""                     # mẫu đã áp riêng cho video này (chỉ để hiển thị)
    #: quy trình duyệt: "" (chưa chuẩn bị) · "pending" (chờ duyệt) · "approved" (đã duyệt) · "skipped" (bỏ qua)
    review: str = ""
    #: cấu hình riêng (các phần của mẫu) — None = dùng cấu hình chung đang mở
    config: dict | None = None
    #: người dùng chọn «Dùng giọng có sẵn» → xuất video dùng thẳng ``voice_path``, không tạo giọng mới
    voice_locked: bool = False

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def stem(self) -> str:
        return Path(self.path).stem

    def persist_dict(self) -> dict[str, Any]:
        d = {"path": self.path, "srt_path": self.srt_path,
             "srt_translated_path": self.srt_translated_path, "voice_path": self.voice_path}
        if self.config is not None:
            d["config"], d["template"] = self.config, self.template
        if self.review:
            d["review"] = self.review
        if self.voice_locked and self.voice_path:
            d["voice_locked"] = True
        if self.status in ("Xong", "Cần xuất lại") and self.output_path:
            d["done"], d["output_path"] = self.status, self.output_path     # mở lại app vẫn biết video đã xuất
        return d


#: các phần cấu hình thuộc về «mẫu» / cấu hình riêng của từng video (không gồm Dựng Truyện, thư mục xuất, settings máy)
TEMPLATE_SECTIONS: dict[str, str] = {
    "video": "Khung hình, tốc độ & âm lượng",
    "subtitle": "Phụ đề, nhận diện giọng & dịch",
    "tts": "Lồng tiếng (giọng đọc)",
    "blur": "Che mờ",
    "bgm": "Nhạc nền",
    "brand": "Logo, chữ & watermark",
    "trim": "Cắt đầu / cuối",
    "color": "Màu, hiệu ứng & chống trùng",
}
_SECTION_CLASSES: dict[str, type] = {}
#: thông số gắn với máy (GPU…) — áp mẫu không đổi
MACHINE_FIELDS = {"video": ("use_gpu", "gpu_backend", "video_codec")}


# ── Tiện ích chuyển đổi ──────────────────────────────────────────────────

def _fold_bgm_master(cfg) -> None:
    """Cấu hình cũ: âm lượng đoạn nhạc × hệ số chung ẩn 20 % (kéo 15 % mà thật ra chỉ 3 %) → gộp hệ số vào từng đoạn,
    hệ số về 100: nghe y như cũ nhưng thanh «Âm lượng» hiện đúng mức thật."""
    m = float(getattr(cfg, "master_volume", 100.0) or 0.0)
    if abs(m - 100.0) < 1e-6:
        return
    for c in cfg.clips or []:
        if isinstance(c, dict):
            c["volume"] = round(float(c.get("volume", 100) or 0) * m / 100.0, 1)
    cfg.master_volume = 100.0


def _from_dict(cls, data: dict[str, Any] | None):
    """Dựng dataclass từ dict, bỏ qua khoá lạ, ép kiểu theo mặc định."""
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        default = getattr(obj, f.name)
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(float(value))
            elif isinstance(default, float):
                value = float(value)
            elif isinstance(default, str) and value is not None:
                value = str(value)
            elif isinstance(default, list):
                value = [dict(v) if isinstance(v, dict) else v for v in value] if isinstance(value, list) else list(default)
        except (TypeError, ValueError):
            continue
        setattr(obj, f.name, value)
    if isinstance(obj, TTSConfig):
        obj.migrate(data)             # cấu hình / mẫu / cấu hình riêng từng video còn ghi engine đã gỡ
    elif isinstance(obj, SubtitleConfig):
        obj.migrate()
    if cls is BGMConfig:
        _fold_bgm_master(obj)
    return obj


_SECTION_CLASSES.update(video=VideoConfig, subtitle=SubtitleConfig, tts=TTSConfig, blur=BlurConfig, bgm=BGMConfig,
                        brand=BrandConfig, trim=TrimConfig, color=ColorConfig)


class AppState:
    """Nguồn sự thật duy nhất cho UI và pipeline.

    Cấu hình đang hiển thị (``self.video``…) là của video đang chọn: video có cấu hình riêng (``item.config``) thì
    nạp cấu hình đó, cấu hình chung được cất ở ``_shared`` và trả lại khi chọn video không có cấu hình riêng."""

    CONFIG_VERSION = 1

    def __init__(self) -> None:
        self.video = VideoConfig()
        self.subtitle = SubtitleConfig()
        self.tts = TTSConfig()
        self.blur = BlurConfig()
        self.bgm = BGMConfig()
        self.brand = BrandConfig()
        self.trim = TrimConfig()
        self.color = ColorConfig()
        self.story = StoryConfig()
        self.settings = Settings()
        self._settings_extra: dict[str, Any] = {}       # khoá lạ trong settings.json (tính năng đã gỡ) — ghi lại y nguyên
        self.settings_notice = ""                        # đọc settings.json gặp chuyện (vd hỏng, dùng bản sao lưu) → báo người dùng
        self.missing_videos: list[str] = []              # mở app: video trong hàng đợi cũ không còn ở ổ đĩa
        #: dòng hàng đợi của video không còn ở ổ đĩa (ổ ngoài chưa cắm…): giữ nguyên trong file cấu hình — cắm lại / nạp
        #: lại video là có lại cấu hình riêng, trạng thái duyệt (trước: lần lưu kế tiếp xoá mất vĩnh viễn)
        self._missing_rows: list[dict] = []
        self._config_backed = False
        self.videos: list[VideoItem] = []
        self.active_index: int = -1
        self.output_dir: str = str(VIDEOS_DIR)
        self.ui: dict[str, Any] = {"active_module": "video"}
        self.template_name: str = ""                    # mẫu đã áp vào cấu hình chung
        self._shared: dict[str, Any] | None = None      # cấu hình chung khi đang chọn video có cấu hình riêng
        # Đang duyệt: chỉnh gì cũng chỉ cho video đang chọn → giữ bản cấu hình chung trước lần chỉnh đầu tiên
        self.review_scope = False
        self._pristine: dict[str, Any] | None = None
        self.last_change_auto = False       # lần config_changed gần nhất do máy tự điền?

        self._listeners: dict[str, list[Callable[..., None]]] = {}
        self._dirty_timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self._save_scheduler: Callable[[Callable[[], None]], None] | None = None

    # ── Sự kiện ──────────────────────────────────────────────────────────
    def on(self, event: str, cb: Callable[..., None]) -> None:
        self._listeners.setdefault(event, []).append(cb)

    def off(self, event: str, cb: Callable[..., None]) -> None:
        try:
            self._listeners.get(event, []).remove(cb)
        except ValueError:
            pass

    def emit(self, event: str, *args: Any) -> None:
        if event in ("config_loaded", "active_changed", "videos_changed"):
            self._snap_pristine()             # áp mẫu / đổi video / nạp cấu hình → mốc «chưa chỉnh» mới
        for cb in list(self._listeners.get(event, [])):
            try:
                cb(*args)
            except Exception:
                logger.exception("Listener %s lỗi", event)

    # ── Video ─────────────────────────────────────────────────────────────
    @property
    def active(self) -> VideoItem | None:
        if 0 <= self.active_index < len(self.videos):
            return self.videos[self.active_index]
        return None

    @staticmethod
    def _item_from_row(row: dict) -> VideoItem:
        """Một dòng hàng đợi đã lưu → VideoItem (file phụ đề / giọng / xuất không còn thì bỏ đường dẫn đó)."""
        has = lambda k: bool(row.get(k)) and Path(row[k]).exists()      # noqa: E731
        it = VideoItem(path=row["path"], srt_path=row["srt_path"] if has("srt_path") else None,
                       srt_translated_path=row["srt_translated_path"] if has("srt_translated_path") else None,
                       voice_path=row["voice_path"] if has("voice_path") else None,
                       config=row.get("config") if isinstance(row.get("config"), dict) else None,
                       template=str(row.get("template") or ""), review=str(row.get("review") or ""))
        it.voice_locked = bool(row.get("voice_locked")) and bool(it.voice_path)
        # trạng thái hiển thị: đã xuất (file xuất còn) → «Xong» / «Cần xuất lại»; không thì theo bước duyệt
        out = row.get("output_path")
        if row.get("done") in ("Xong", "Cần xuất lại") and isinstance(out, str) and Path(out).exists():
            it.status, it.output_path = row["done"], out
        else:
            it.status = {"pending": "Chờ duyệt", "approved": "Đã duyệt", "skipped": "Bỏ qua"}.get(it.review, it.status)
        return it

    def add_videos(self, paths: list[str]) -> list[VideoItem]:
        added: list[VideoItem] = []
        existing = {v.path.lower() for v in self.videos}
        for p in paths:
            p = str(Path(p))
            if p.lower() in existing:
                continue
            row = next((r for r in self._missing_rows if str(Path(r["path"])).lower() == p.lower()), None)
            if row is not None:                     # video từng nằm ở ổ chưa cắm → lấy lại cấu hình riêng / duyệt
                self._missing_rows.remove(row)
                item = self._item_from_row(row)
            else:
                item = VideoItem(path=p)
            self.videos.append(item)
            existing.add(p.lower())
            added.append(item)
        if added:
            first = self.active_index < 0
            if first:
                self._activate(0, None)
            self.emit("videos_changed")
            if first:                       # hàng đợi đang trống → video đầu tiên thành video đang xem
                self.emit("active_changed")
            self.mark_dirty()
        return added

    def remove_video(self, index: int) -> None:
        if not (0 <= index < len(self.videos)):
            return
        prev = self.active
        del self.videos[index]
        new = self.active_index
        if index < new:
            new -= 1
        new = min(new, len(self.videos) - 1)
        self._activate(new, prev)
        self.emit("videos_changed")
        self.emit("active_changed")
        self.mark_dirty()

    def set_for(self, items: list[VideoItem], section: str, field: str, value: Any) -> None:
        """Đặt một giá trị cấu hình cho ``items``: video chỉnh riêng → cấu hình riêng của nó; còn lại → cấu hình chung."""
        shared = False
        for it in items:
            if it.config is None:
                shared = True
            elif it is self.active:
                setattr(getattr(self, section), field, value)
            else:
                it.config.setdefault(section, {})[field] = value
        if shared:
            if self._shared is not None:               # đang hiện cấu hình riêng → cấu hình chung đang cất riêng
                self._shared.setdefault(section, {})[field] = value
            else:
                setattr(getattr(self, section), field, value)
        self.config_changed(section, auto=True)

    def move_video(self, index: int, to: int) -> None:
        """Đổi thứ tự hàng đợi (thứ tự = mã {stt} trong tên file xuất); video đang chọn vẫn là nó."""
        if not (0 <= index < len(self.videos)):
            return
        to = max(0, min(len(self.videos) - 1, to))
        if to == index:
            return
        act = self.active
        self.videos.insert(to, self.videos.pop(index))
        if act is not None:
            self.active_index = self.videos.index(act)
        self.emit("videos_changed")
        self.emit("active_changed")
        self.mark_dirty()

    def clear_videos(self) -> None:
        prev = self.active
        self.videos.clear()
        self._missing_rows = []                        # «Xoá hết» = bỏ luôn dòng của video ở ổ chưa cắm
        self._activate(-1, prev)
        self.emit("videos_changed")
        self.emit("active_changed")
        self.mark_dirty()

    def set_active(self, index: int) -> None:
        if index != self.active_index and 0 <= index < len(self.videos):
            self._activate(index, self.active)
            self.emit("active_changed")

    # ── Cấu hình riêng từng video ────────────────────────────────────────
    def sections_dict(self) -> dict[str, Any]:
        """Bản sao các phần cấu hình đang hiển thị."""
        return {k: dataclasses.asdict(getattr(self, k)) for k in TEMPLATE_SECTIONS}

    def apply_sections(self, data: dict[str, Any], only: list[str] | tuple[str, ...] | None = None,
                       keep_machine: bool = False) -> None:
        for k in only or TEMPLATE_SECTIONS:
            if k not in TEMPLATE_SECTIONS or not isinstance(data.get(k), dict):
                continue
            new = _from_dict(_SECTION_CLASSES[k], data[k])
            if keep_machine:
                for f in MACHINE_FIELDS.get(k, ()):
                    setattr(new, f, getattr(getattr(self, k), f))
            setattr(self, k, new)

    def _activate(self, index: int, prev: VideoItem | None) -> bool:
        """Đổi video đang chọn + đổi cấu hình hiển thị theo video (riêng / chung). Trả về True nếu cấu hình đã đổi."""
        if prev is not None and prev.config is not None and prev in self.videos:
            prev.config = self.sections_dict()              # lưu chỉnh sửa của video vừa rời
        self.active_index = index
        new = self.active
        changed = False
        if new is not None and new.config is not None:
            if self._shared is None:
                self._shared = self.sections_dict()        # cất cấu hình chung
            self.apply_sections(new.config)
            changed = True
        elif self._shared is not None:
            self.apply_sections(self._shared)
            self._shared = None
            changed = True
        if changed:
            self.emit("config_loaded")
        return changed

    def shared_sections(self) -> dict[str, Any]:
        """Cấu hình chung (cho video không có cấu hình riêng)."""
        return self._shared if self._shared is not None else self.sections_dict()

    def effective_sections(self, item: VideoItem) -> dict[str, Any]:
        """Cấu hình thật sự dùng để render ``item``."""
        if item.config is not None:
            return self.sections_dict() if item is self.active else item.config
        return self.shared_sections()

    def make_own(self, item: VideoItem, sections: dict[str, Any] | None = None, template: str = "") -> None:
        """Cho ``item`` cấu hình riêng (mặc định: chép cấu hình đang hiển thị; ``sections``: các phần muốn đè)."""
        import copy

        cfg = copy.deepcopy(self.effective_sections(item))
        for k, v in (sections or {}).items():
            if k in TEMPLATE_SECTIONS and isinstance(v, dict):
                merged = dict(v)
                for f in MACHINE_FIELDS.get(k, ()):
                    merged[f] = cfg.get(k, {}).get(f, merged.get(f))
                cfg[k] = merged
        if item is self.active and item.config is None and self._shared is None:
            self._shared = self.sections_dict()
        item.config = cfg
        item.template = template or item.template
        if item is self.active:
            self.apply_sections(cfg)
            self.emit("config_loaded")
        self.mark_dirty()

    def drop_own(self, item: VideoItem) -> None:
        """Bỏ cấu hình riêng → video dùng lại cấu hình chung."""
        if item.config is None:
            return
        item.config, item.template = None, ""
        if item is self.active and self._shared is not None:
            self.apply_sections(self._shared)
            self._shared = None
            self.emit("config_loaded")
        self.mark_dirty()

    def state_for(self, item: VideoItem) -> AppState:
        """Bản sao trạng thái để render ``item`` theo đúng cấu hình của nó (an toàn khi UI đổi cấu hình lúc đang chạy)."""
        import copy

        clone = copy.copy(self)
        clone._listeners = {}
        clone._save_scheduler = None
        clone._dirty_timer = None
        clone._shared = None
        clone.apply_sections(copy.deepcopy(self.effective_sections(item)))
        clone.story = copy.deepcopy(self.story)
        return clone

    # ── Chỉnh riêng khi duyệt ─────────────────────────────────────────────
    def set_review_scope(self, on: bool) -> None:
        self.review_scope = bool(on)
        self._snap_pristine()

    def _snap_pristine(self) -> None:
        a = self.active
        self._pristine = self.sections_dict() if (self.review_scope and a is not None and a.config is None) else None

    def _scope_edit(self) -> None:
        """Lần chỉnh đầu tiên trên video đang duyệt (đang theo cấu hình chung) → tách thành cấu hình riêng của video
        đó; cấu hình chung (cho các video khác) trở về như trước lần chỉnh."""
        a = self.active
        if not self.review_scope or self._pristine is None or a is None or a.config is not None:
            return
        if not a.review:
            # video chưa chuẩn bị: chỉnh lúc này là chỉnh cấu hình chung (cho cả hàng đợi) — chụp lại mốc «chưa chỉnh»
            self._snap_pristine()
            return
        cur = self.sections_dict()
        if cur == self._pristine:
            return
        a.config = cur
        a.template = a.template or self.template_name
        self._shared, self._pristine = self._pristine, None
        self.emit("scoped", a)

    def apply_to_all(self, item: VideoItem) -> None:
        """Nút «Áp cho tất cả»: cấu hình riêng của ``item`` (đang chọn) thành cấu hình chung, bỏ «riêng» của nó.
        Video khác đã chỉnh riêng giữ nguyên."""
        if item is not self.active or item.config is None:
            return
        item.config, item.template = None, ""
        self._shared = None                   # cấu hình đang hiển thị (của item) giờ là cấu hình chung
        self.emit("config_loaded")
        self.mark_dirty()

    # ── Cấu hình ──────────────────────────────────────────────────────────
    def config_changed(self, section: str = "", auto: bool = False) -> None:
        """Module UI gọi sau khi ghi một giá trị vào cấu hình. ``auto``: máy tự điền (vd. gợi ý giọng cho người nói)
        chứ không phải người dùng sửa → không tách video thành «chỉnh riêng», mốc «chưa chỉnh» nhận luôn giá trị mới."""
        self.last_change_auto = auto
        if auto:
            if self._pristine is not None:
                self._snap_pristine()
        else:
            self.ui["edited"] = True                   # người dùng đã tự chỉnh (áp mẫu thì về False)
            self._scope_edit()
        self.emit("config_changed", section)
        self.mark_dirty()

    # ── Lưu / nạp ─────────────────────────────────────────────────────────
    def set_save_scheduler(self, scheduler: Callable[[Callable[[], None]], None]) -> None:
        """UI cung cấp hàm chạy-sau-N-ms (root.after) để autosave không cần thread."""
        self._save_scheduler = scheduler

    def mark_dirty(self) -> None:
        if self._save_scheduler is not None:
            self._save_scheduler(self.save_config)
            return
        with self._lock:
            if self._dirty_timer is not None:
                self._dirty_timer.cancel()
            self._dirty_timer = threading.Timer(0.8, self.save_config)
            self._dirty_timer.daemon = True
            self._dirty_timer.start()

    def to_config_dict(self) -> dict[str, Any]:
        act = self.active
        if act is not None and act.config is not None:
            act.config = self.sections_dict()               # cấu hình riêng của video đang chọn = cái đang hiển thị
        return {
            "_meta": {"app": "edit_studio", "version": self.CONFIG_VERSION},
            **self.shared_sections(),                       # cấu hình chung (không phải của video đang chọn)
            "story": dataclasses.asdict(self.story),
            "template": self.template_name,
            "videos": [v.persist_dict() for v in self.videos] + list(self._missing_rows),
            "active_index": self.active_index,
            "output_dir": self.output_dir,
            "ui": dict(self.ui),
        }

    def apply_config_dict(self, data: dict[str, Any], *, include_videos: bool = True) -> None:
        self.video = _from_dict(VideoConfig, data.get("video"))
        self.subtitle = _from_dict(SubtitleConfig, data.get("subtitle"))
        self.tts = _from_dict(TTSConfig, data.get("tts"))
        self.blur = _from_dict(BlurConfig, data.get("blur"))
        self.bgm = _from_dict(BGMConfig, data.get("bgm"))
        self.brand = _from_dict(BrandConfig, data.get("brand"))
        self.trim = _from_dict(TrimConfig, data.get("trim"))
        self.color = _from_dict(ColorConfig, data.get("color"))
        self.story = _from_dict(StoryConfig, data.get("story"))
        # cấu hình cũ còn ghi model Google đã tắt (2.5 Flash, Imagen 4, Veo 3.0…) → tự đổi sang model thay thế
        for obj, attr in ((self.story, "text_model"), (self.story, "image_model"), (self.story, "video_model")):
            setattr(obj, attr, migrate_model(getattr(obj, attr)))
        if self.story.provider in RETIRED_TTS:
            self.story.provider = VIENEU
            # giọng Gemini nam → giọng nam VieNeu; còn lại (cả giọng trả phí) → giọng nữ
            self.story.voice_id = self.story.voice_name = vieneu_for_gemini(self.story.voice_id)
            self.story.voice_language = "vi-VN"
        if include_videos:
            self._shared = None
            self.template_name = str(data.get("template") or "")
            self.videos = []
            self.missing_videos = []
            self._missing_rows = []
            for row in data.get("videos", []) or []:
                if not isinstance(row, dict) or not row.get("path"):
                    continue
                if not Path(row["path"]).exists():
                    self.missing_videos.append(Path(row["path"]).name)
                    self._missing_rows.append(row)
                    continue
                self.videos.append(self._item_from_row(row))
            idx = data.get("active_index", 0)
            idx = idx if isinstance(idx, int) and 0 <= idx < len(self.videos) else (0 if self.videos else -1)
            self.active_index = -1
            self._activate(idx, None)                   # video đang chọn có cấu hình riêng → nạp cấu hình đó
        out = data.get("output_dir")
        if isinstance(out, str) and out:
            self.output_dir = out
        ui = data.get("ui")
        if isinstance(ui, dict):
            self.ui.update(ui)

    def save_config(self, path: Path | None = None) -> None:
        target = path or CONFIG_FILE
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not self._config_backed and path is None and target.exists():
                # lần lưu đầu mỗi phiên: giữ bản trước khi mở app (lỡ tay xoá / ghi đè hàng đợi vẫn lấy lại được)
                import shutil

                shutil.copyfile(target, target.with_suffix(".json.bak"))
                self._config_backed = True
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_config_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)
            logger.debug("Đã lưu cấu hình: %s", target)
        except Exception:
            logger.exception("Không lưu được cấu hình")

    def load_config(self, path: Path | None = None) -> bool:
        target = path or CONFIG_FILE
        if not target.exists():
            return False
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            self.apply_config_dict(data)
            logger.info("Đã nạp cấu hình: %s (%d video)", target, len(self.videos))
            return True
        except Exception:
            logger.exception("Cấu hình hỏng, dùng mặc định: %s", target)
            return False

    # ── Settings máy ──────────────────────────────────────────────────────
    def load_settings(self) -> None:
        self.settings_notice = ""
        if SETTINGS_FILE.exists():
            try:
                try:
                    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                except ValueError:
                    # file bị cắt dở (mất điện lúc ghi) → giữ lại bản hỏng, dùng bản sao lưu lần lưu trước (còn key)
                    bak = SETTINGS_FILE.with_suffix(".json.bak")
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    SETTINGS_FILE.replace(SETTINGS_FILE.with_suffix(".json.hong"))
                    self.settings_notice = "settings.json bị hỏng — đã dùng bản sao lưu lần lưu trước."
                    logger.warning(self.settings_notice)
                self.settings = _from_dict(Settings, data)
                if isinstance(data, dict):
                    # key / cài đặt của tính năng đã gỡ (Groq, ElevenLabs…) giữ nguyên trong file, không xoá
                    known = {f.name for f in fields(Settings)}
                    self._settings_extra = {k: v for k, v in data.items() if k not in known}
                    if data.get("translate_provider", "Gemini") != "Gemini":   # model của nhà cung cấp khác → Gemini
                        self.settings.translate_model = Settings.translate_model
                    # một ô key Gemini: chuyển key ở ô key đơn cũ + ô «Google API keys» (vẫn được dùng làm key Gemini)
                    moved = [self._settings_extra.pop(k, "") for k in ("gemini_api_key", "google_api_key")]
                    keys = Settings.split_keys("\n".join([self.settings.gemini_api_keys, *(str(v or "") for v in moved)]))
                    self.settings.gemini_api_keys = "\n".join(keys)
                self.settings.translate_model = migrate_model(self.settings.translate_model)
            except Exception:
                logger.exception("settings.json hỏng, dùng mặc định")

    def save_settings(self) -> bool:
        """Ghi settings.json (có API key) qua file tạm rồi đổi tên — mất điện giữa chừng không làm hỏng file; bản cũ
        giữ ở settings.json.bak. False = không ghi được (ổ đầy, file bị khoá…)."""
        try:
            data = {**self._settings_extra, **dataclasses.asdict(self.settings)}
            tmp = SETTINGS_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            if SETTINGS_FILE.exists():
                shutil.copyfile(SETTINGS_FILE, SETTINGS_FILE.with_suffix(".json.bak"))
            tmp.replace(SETTINGS_FILE)
            return True
        except Exception:
            logger.exception("Không lưu được settings.json")
            return False
