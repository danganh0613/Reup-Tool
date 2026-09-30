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
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from app.config.paths import CONFIG_FILE, PRESETS_DIR, SETTINGS_FILE, VIDEOS_DIR

logger = logging.getLogger(__name__)

# ── Hằng lựa chọn (đồng thời là nhãn hiển thị) ────────────────────────────
RATIOS = ["Giữ nguyên", "21:9 (Điện ảnh)", "16:9 (Ngang)", "4:3", "1:1 (Vuông)", "3:4", "4:5 (Dọc ngắn)", "9:16 (Dọc)"]
RATIO_VALUES: dict[str, float | None] = {"Giữ nguyên": None, "21:9 (Điện ảnh)": 21 / 9, "16:9 (Ngang)": 16 / 9, "4:3": 4 / 3,
                                         "1:1 (Vuông)": 1.0, "3:4": 3 / 4, "4:5 (Dọc ngắn)": 4 / 5, "9:16 (Dọc)": 9 / 16}
FIT_MODES = ["Cắt vừa khung", "Thêm viền đen", "Nền mờ (blur)"]
RESOLUTIONS = ["Giữ nguyên", "720p", "1080p", "1440p", "4K"]
FPS_OPTIONS = ["Giữ nguyên", "24", "25", "30", "50", "60"]
GPU_BACKENDS = ["auto", "nvenc", "qsv", "amf", "cpu"]
VIDEO_CODECS = ["H.264 / AVC", "H.265 / HEVC", "AV1"]
ENCODE_PRESETS = ["Nhanh", "Cân bằng", "Chất lượng"]

SRT_SOURCES = ["Tự nhận diện (Whisper)", "Chọn file SRT"]
STT_ENGINES = ["Whisper (local)", "Gemini (cloud)"]
TEXT_EFFECTS = ["Không có (Khuyên dùng)", "Viền đậm", "Bóng đổ", "Viền + bóng", "Phát sáng (glow)"]
SUB_BG_STYLES = ["Không nền", "Hộp mờ bo góc", "Hộp đen", "Dải ngang toàn khung", "Bóng mềm"]
TRANSLATE_PROVIDERS = ["Gemini", "Groq", "DeepSeek", "OpenAI"]
TRIM_MODES = ["Ghép các đoạn thành 1 video", "Xuất mỗi đoạn thành 1 file"]
COLOR_PRESETS = ["Mặc định", "Ấm áp", "Điện ảnh", "Tươi sáng", "Cổ điển", "Lạnh"]
STT_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
STT_DEVICES = ["auto", "cpu", "cuda"]
STT_LANGUAGES = ["auto", "vi", "en", "zh", "ja", "ko", "fr", "de", "es", "th", "id", "ru"]
SRT_MODES = ["Sub gốc", "Sub đã dịch", "Song ngữ (dịch trên, gốc dưới)"]
SUB_POSITIONS = ["Dưới", "Giữa", "Trên", "Tuỳ chỉnh (X/Y)"]
TRANSLATE_TARGETS = ["Tiếng Việt", "English", "中文", "日本語", "한국어", "Français", "Deutsch",
                     "Español", "ไทย", "Bahasa Indonesia", "Русский"]
AI_ENGINES = ["Gemini"]
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
                 "gemini-flash-lite-latest", "gemini-pro-latest"]

TEXTURE_BLENDS = ["screen", "overlay", "softlight", "multiply", "lighten", "addition"]
BLUR_STYLES = ["Mờ (Gaussian)", "Ô vuông (pixelate)", "Che kín (màu đen)"]
MOTIONS = ["Đứng yên", "Trôi ngang", "Trôi dọc", "Nảy (bounce)", "Đường chéo", "Nhấp nháy"]
POSITIONS = ["Trên trái", "Trên giữa", "Trên phải", "Giữa trái", "Chính giữa", "Giữa phải",
             "Dưới trái", "Dưới giữa", "Dưới phải"]
SAFE_ZONES = ["Toàn khung", "TikTok/Reels (né UI phải & dưới)"]
KENBURNS = ["Không", "Zoom vào", "Zoom ra", "Trái → phải", "Phải → trái", "Trên → dưới"]

TTS_PROVIDERS = ["Edge TTS", "ElevenLabs", "Google Cloud TTS", "VieNeu (local, tiếng Việt)"]
TTS_SOURCES = ["Sub đã dịch", "Sub gốc"]
ELEVEN_MODELS = ["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_v3"]


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
    loop_to_voice: bool = False        # lặp video cho đủ thời lượng giọng đọc
    gop_size: int = 0                  # khoảng keyframe (0 = mặc định encoder)
    demucs_enable: bool = False        # tách giọng gốc bằng Demucs (cần cài thêm)


@dataclass
class SubtitleConfig:
    enable_subtitle: bool = True
    srt_source: str = SRT_SOURCES[0]
    stt_model: str = "base"
    stt_language: str = "auto"
    stt_device: str = "auto"
    translate_enable: bool = False
    ai_engine: str = AI_ENGINES[0]
    gemini_model: str = GEMINI_MODELS[0]
    translate_target: str = TRANSLATE_TARGETS[0]
    translate_style: str = ""          # ghi chú phong cách dịch (tuỳ chọn)
    srt_mode: str = SRT_MODES[0]
    sub_font: str = "Arial"
    sub_size: int = 36
    sub_bold: bool = True
    sub_italic: bool = False
    sub_uppercase: bool = False
    sub_color: str = "#FFFFFF"
    sub_opacity: float = 100.0
    sub_stroke_width: float = 2.0
    sub_stroke_color: str = "#000000"
    sub_shadow: float = 0.0
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
    # Khung phụ đề (tỉ lệ 0..1 theo khung ra): chữ tự xuống dòng theo bề rộng khung, căn đáy khung
    use_box: bool = True
    box_x: float = 0.05
    box_y: float = 0.74
    box_w: float = 0.90
    box_h: float = 0.14
    text_effect: str = "Không có (Khuyên dùng)"
    bg_style: str = "Không nền"
    karaoke: bool = False
    karaoke_color: str = "#FFD400"
    stt_engine: str = "Whisper (local)"
    weak_pc: bool = False
    ai_reflow: bool = False            # AI tối ưu câu + gộp timeline
    translate_source: str = "Tự nhận diện"


@dataclass
class TTSConfig:
    enable_tts: bool = False
    tts_provider: str = TTS_PROVIDERS[0]
    tts_source: str = TTS_SOURCES[0]
    edge_language: str = "vi-VN"
    edge_voice: str = "vi-VN-HoaiMyNeural"
    eleven_voice_id: str = ""
    eleven_voice_name: str = ""
    eleven_model: str = ELEVEN_MODELS[0]
    eleven_stability: float = 0.5
    eleven_similarity: float = 0.75
    eleven_style: float = 0.0
    eleven_speaker_boost: bool = True
    google_language: str = "vi-VN"
    google_voice: str = "vi-VN-Neural2-A"
    vieneu_voice: str = "Minh Quân Pro"
    tts_speed: float = 1.0
    tts_pitch: int = 0                 # Hz, Edge only
    max_tempo: float = 1.5             # tăng tốc tối đa để khớp thời lượng cue
    min_gap: float = 0.08              # khoảng nghỉ tối thiểu giữa 2 cue (giây)
    natural_sync: bool = False         # đọc tốc độ tự nhiên, dời phụ đề theo giọng (không tăng tốc)
    soft_timing: bool = False          # dời câu vào khoảng lặng, nén tối đa 1.10×


@dataclass
class BlurConfig:
    enable: bool = False
    #: mỗi vùng: {x, y, w, h (0..1 theo khung ra), strength, feather, opacity, style, start, end}
    regions: list = field(default_factory=list)
    active_index: int = 0


@dataclass
class BGMConfig:
    enable: bool = False
    #: mỗi clip: {path, volume, start, offset, loop, fade_in, fade_out}
    clips: list = field(default_factory=list)
    master_volume: float = 20.0
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
    watermark_text: str = "@kenh_cua_ban"
    watermark_font: str = "Arial"
    watermark_size: int = 30
    watermark_color: str = "#FFFFFF"
    watermark_opacity: float = 45.0
    watermark_motion: str = MOTIONS[3]
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
    image_model: str = "gemini-2.5-flash-image"
    video_model: str = "veo-3.0-fast-generate-001"
    video_duration: int = 8
    use_ai_video: bool = False
    ratio: str = STORY_RATIOS[0]
    resolution: str = STORY_RESOLUTIONS[1]
    provider: str = "Edge TTS"
    voice_language: str = "vi-VN"
    voice_id: str = "vi-VN-HoaiMyNeural"
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
    gemini_api_key: str = ""
    elevenlabs_api_key: str = ""
    google_api_key: str = ""
    appearance_mode: str = "dark"      # dark | oled
    accent: str = "lime"
    theme: str = "studio"             # studio · midnight · graphite · orchid · oled
    output_dir: str = ""
    windows_toast: bool = True
    tts_workers: int = 4
    whisper_compute: str = "auto"      # auto | int8 | float16 | float32
    vieneu_precision: str = "int8"     # int8 (nhanh) | fp32 (chất lượng)
    translate_provider: str = "Gemini"
    translate_model: str = "gemini-flash-latest"
    gemini_api_keys: str = ""          # nhiều key, mỗi dòng một key (xoay vòng khi hết quota)
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    glossary: str = ""                 # mỗi dòng: từ nguồn=nghĩa
    elevenlabs_api_keys: str = ""      # nhiều key, mỗi dòng một key

    @staticmethod
    def split_keys(text: str) -> list[str]:
        """Ô key nhiều dòng → danh sách key (mỗi dòng / dấu phẩy một key, bỏ trùng, giữ thứ tự)."""
        raw = (text or "").replace(",", "\n").replace(";", "\n")
        return list(dict.fromkeys(k.strip() for k in raw.splitlines() if k.strip()))

    def gemini_keys(self) -> list[str]:
        """Key Gemini theo thứ tự ưu tiên: ô nhiều key → ô key đơn → các Google API key (cùng loại AIza…, dùng chung)."""
        out = self.split_keys(self.gemini_api_keys)
        for k in [self.gemini_api_key, *self.split_keys(self.google_api_key)]:
            k = (k or "").strip()
            if k and k not in out:
                out.append(k)
        return out

    def google_keys(self) -> list[str]:
        """Google Cloud API key (TTS): mỗi dòng một key, xoay vòng khi hết hạn mức."""
        return self.split_keys(self.google_api_key)
    google_sa_json: str = ""           # đường dẫn service account JSON (thay cho API key)
    show_waveform: bool = True
    show_guide_on_start: bool = True     # mở Hướng dẫn ở lần chạy đầu (tắt trong cửa sổ hướng dẫn)
    ffmpeg_fast: bool = False
    gpu_encoder: str = "auto"


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

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def stem(self) -> str:
        return Path(self.path).stem

    def persist_dict(self) -> dict[str, Any]:
        return {"path": self.path, "srt_path": self.srt_path,
                "srt_translated_path": self.srt_translated_path, "voice_path": self.voice_path}


# ── Tiện ích chuyển đổi ──────────────────────────────────────────────────

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
    return obj


class AppState:
    """Nguồn sự thật duy nhất cho UI và pipeline."""

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
        self.videos: list[VideoItem] = []
        self.active_index: int = -1
        self.output_dir: str = str(VIDEOS_DIR)
        self.ui: dict[str, Any] = {"active_module": "video"}

        self._listeners: dict[str, list[Callable[..., None]]] = {}
        self._dirty_timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self._save_scheduler: Callable[[Callable[[], None]], None] | None = None

    # ── Sự kiện ──────────────────────────────────────────────────────────
    def on(self, event: str, cb: Callable[..., None]) -> None:
        self._listeners.setdefault(event, []).append(cb)

    def emit(self, event: str, *args: Any) -> None:
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

    def add_videos(self, paths: list[str]) -> list[VideoItem]:
        added: list[VideoItem] = []
        existing = {v.path.lower() for v in self.videos}
        for p in paths:
            p = str(Path(p))
            if p.lower() in existing:
                continue
            item = VideoItem(path=p)
            self.videos.append(item)
            existing.add(p.lower())
            added.append(item)
        if added:
            if self.active_index < 0:
                self.active_index = 0
            self.emit("videos_changed")
            self.mark_dirty()
        return added

    def remove_video(self, index: int) -> None:
        if not (0 <= index < len(self.videos)):
            return
        del self.videos[index]
        if self.active_index >= len(self.videos):
            self.active_index = len(self.videos) - 1
        self.emit("videos_changed")
        self.emit("active_changed")
        self.mark_dirty()

    def clear_videos(self) -> None:
        self.videos.clear()
        self.active_index = -1
        self.emit("videos_changed")
        self.emit("active_changed")
        self.mark_dirty()

    def set_active(self, index: int) -> None:
        if index != self.active_index and 0 <= index < len(self.videos):
            self.active_index = index
            self.emit("active_changed")

    # ── Cấu hình ──────────────────────────────────────────────────────────
    def config_changed(self, section: str = "") -> None:
        """Module UI gọi sau khi ghi một giá trị vào cấu hình."""
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
        return {
            "_meta": {"app": "edit_studio", "version": self.CONFIG_VERSION},
            "video": dataclasses.asdict(self.video),
            "subtitle": dataclasses.asdict(self.subtitle),
            "tts": dataclasses.asdict(self.tts),
            "blur": dataclasses.asdict(self.blur),
            "bgm": dataclasses.asdict(self.bgm),
            "brand": dataclasses.asdict(self.brand),
            "trim": dataclasses.asdict(self.trim),
            "color": dataclasses.asdict(self.color),
            "story": dataclasses.asdict(self.story),
            "videos": [v.persist_dict() for v in self.videos],
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
        if include_videos:
            self.videos = []
            for row in data.get("videos", []) or []:
                if not isinstance(row, dict) or not row.get("path"):
                    continue
                if not Path(row["path"]).exists():
                    continue
                self.videos.append(VideoItem(
                    path=row["path"],
                    srt_path=row.get("srt_path") if row.get("srt_path") and Path(row["srt_path"]).exists() else None,
                    srt_translated_path=row.get("srt_translated_path")
                    if row.get("srt_translated_path") and Path(row["srt_translated_path"]).exists() else None,
                    voice_path=row.get("voice_path") if row.get("voice_path") and Path(row["voice_path"]).exists() else None,
                ))
            idx = data.get("active_index", 0)
            self.active_index = idx if isinstance(idx, int) and 0 <= idx < len(self.videos) else (0 if self.videos else -1)
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

    # ── Preset ────────────────────────────────────────────────────────────
    def list_presets(self) -> list[str]:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))

    def save_preset(self, name: str) -> Path:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "preset"
        target = PRESETS_DIR / f"{safe}.json"
        data = self.to_config_dict()
        data.pop("videos", None)
        data.pop("active_index", None)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def load_preset(self, name: str) -> bool:
        target = PRESETS_DIR / f"{name}.json"
        if not target.exists():
            return False
        data = json.loads(target.read_text(encoding="utf-8"))
        self.apply_config_dict(data, include_videos=False)
        self.emit("config_loaded")
        self.mark_dirty()
        return True

    # ── Settings máy ──────────────────────────────────────────────────────
    def load_settings(self) -> None:
        if SETTINGS_FILE.exists():
            try:
                self.settings = _from_dict(Settings, json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except Exception:
                logger.exception("settings.json hỏng, dùng mặc định")

    def save_settings(self) -> None:
        try:
            SETTINGS_FILE.write_text(json.dumps(dataclasses.asdict(self.settings), ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        except Exception:
            logger.exception("Không lưu được settings.json")
