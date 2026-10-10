"""Mẫu (template) dựng video: một bộ thiết lập có tên, lưu được từng phần, áp cho cả hàng đợi hoặc từng video.

- Mẫu dựng sẵn (``BUILTIN``): các kiểu reup đang thịnh hành — chỉ đè những thông số của kiểu đó (patch), giữ nguyên
  logo / che mờ / nhạc nền / giọng đọc bạn đã chọn.
- Mẫu của bạn: ảnh chụp đầy đủ các phần đã chọn khi lưu (khung hình, phụ đề, giọng, logo…), kèm ảnh đại diện.
- Lưu ở ``presets/templates/<tên>.json`` (+ ``.jpg``). Xuất / nhập một file ``.json`` duy nhất (ảnh nhúng base64).
- Preset cũ (``presets/*.json``) được chuyển sang mẫu ở lần chạy đầu.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.paths import PRESETS_DIR
from app.core.state import (FIT_MODES, MACHINE_FIELDS, SRT_MODES, SRT_SOURCES, TEMPLATE_SECTIONS,
                            TEXT_EFFECTS, TTS_SOURCES, AppState, VideoItem)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = PRESETS_DIR / "templates"
NO_TEMPLATE = "— Cấu hình tự chỉnh —"
#: thông số máy / nhận diện giọng: mẫu không đè (model Whisper, GPU… mỗi máy một khác)
KEEP_FIELDS: dict[str, tuple[str, ...]] = {
    **MACHINE_FIELDS,
    "subtitle": ("stt_model", "stt_device", "stt_engine", "weak_pc"),
}


@dataclass
class Template:
    name: str
    sections: dict[str, dict] = field(default_factory=dict)
    parts: list[str] = field(default_factory=list)
    description: str = ""
    builtin: bool = False
    patch: bool = False                 # True: chỉ đè các trường có trong mẫu (mẫu dựng sẵn)
    badge: str = ""                     # chữ ngắn trên ảnh đại diện mẫu dựng sẵn (9:16, 16:9…)
    thumb: Path | None = None

    def parts_label(self) -> str:
        return ", ".join(TEMPLATE_SECTIONS[p].split(",")[0].split(" (")[0] for p in self.parts if p in TEMPLATE_SECTIONS)


# ── Mẫu dựng sẵn ──────────────────────────────────────────────────────────

_COLOR_OFF = {"enable": False, "dup_shift": False, "dup_noise": False, "dup_color": False, "dup_zoompan": False,
              "dup_audio": False, "dup_gop": False, "texture_enable": False, "kenburns": "Không", "deshake": False}

BUILTIN: list[Template] = [
    Template(
        name="🔥 Review phim dọc 9:16 (TikTok · Shorts · Reels)",
        description="Video ngang → dọc 9:16 nền mờ, phụ đề Việt chữ vàng viền đen to rõ, lồng tiếng Việt đọc nhanh "
                    "(khớp mềm), mỗi nhân vật một giọng (tự nhận diện người nói), tự dò & che phụ đề gốc in sẵn, khử giọng nói gốc bằng AI (giữ nhạc nền & tiếng động), tăng tốc 105% + chống trùng nhẹ (dịch khung, lệch màu, lệch tiếng, GOP).",
        badge="9:16", builtin=True, patch=True,
        parts=["video", "subtitle", "tts", "blur", "trim", "color"],
        sections={
            "video": {"ratio": "9:16 (Dọc)", "fit_mode": FIT_MODES[2], "resolution": "1080p", "fps": "30", "zoom": 100,
                      "speed": 105, "flip_h": False, "mute": False, "keep_original_audio": True, "vocal_filter": False,
                      "demucs_enable": True, "original_volume": 70.0, "tts_volume": 160.0, "adaptive_ducking": True, "normalize_audio": True,
                      "output_suffix": "_review", "loop_to_voice": False},
            "subtitle": {"enable_subtitle": True, "srt_source": SRT_SOURCES[0], "translate_enable": True,
                         "translate_target": "Tiếng Việt", "srt_mode": SRT_MODES[1], "sub_size": 50, "sub_bold": True,
                         "sub_italic": False, "sub_uppercase": False, "sub_color": "#FFE45C", "sub_opacity": 100.0,
                         "sub_stroke_width": 3.0, "sub_stroke_color": "#000000", "sub_shadow": 1.0,
                         "sub_bg_enable": False, "use_box": True, "box_x": 0.06, "box_y": 0.70, "box_w": 0.88,
                         "box_h": 0.06, "follow_hardsub": True, "text_effect": TEXT_EFFECTS[1], "bg_style": "Không nền", "karaoke": False},
            "tts": {"enable_tts": True, "tts_source": TTS_SOURCES[0], "soft_timing": True, "natural_sync": False,
                    "tts_speed": 1.1, "max_tempo": 1.4, "multi_voice": True, "speaker_count": "Tự động"},
            "blur": {"auto_detect": True},             # tự che phụ đề tiếng Trung in sẵn
            "trim": {"enable": False, "head": "0", "tail": "0", "segments": []},
            "color": {**_COLOR_OFF, "enable": True, "contrast": 1.05, "saturation": 1.1, "sharpen": 0.3,
                      "brightness": 0.0, "gamma": 1.0, "temperature": 0.0, "grain": 0.0, "vignette": 0.0,
                      "dup_shift": True, "dup_color": True, "dup_audio": True, "dup_gop": True},
        }),
    Template(
        name="🎙 Thuyết minh phim ngang 16:9 (YouTube)",
        description="Giữ khung ngang 16:9 (thêm viền đen nếu lệch tỉ lệ), phụ đề Việt trắng trên hộp mờ, lồng tiếng "
                    "thuyết minh — mỗi nhân vật một giọng (tự nhận diện người nói), khử giọng nói gốc bằng AI, giữ nhạc nền & tiếng động của phim, tự dò & che phụ đề gốc in sẵn.",
        badge="16:9", builtin=True, patch=True,
        parts=["video", "subtitle", "tts", "blur", "trim", "color"],
        sections={
            "video": {"ratio": "16:9 (Ngang)", "fit_mode": FIT_MODES[1], "resolution": "1080p", "fps": "Giữ nguyên",
                      "zoom": 100, "speed": 100, "flip_h": False, "mute": False, "keep_original_audio": True,
                      "vocal_filter": False, "demucs_enable": True, "original_volume": 75.0, "tts_volume": 150.0, "adaptive_ducking": True,
                      "normalize_audio": True, "output_suffix": "_thuyetminh", "loop_to_voice": False},
            "subtitle": {"enable_subtitle": True, "srt_source": SRT_SOURCES[0], "translate_enable": True,
                         "translate_target": "Tiếng Việt", "srt_mode": SRT_MODES[1], "sub_size": 40, "sub_bold": True,
                         "sub_italic": False, "sub_uppercase": False, "sub_color": "#FFFFFF", "sub_opacity": 100.0,
                         "sub_stroke_width": 1.5, "sub_stroke_color": "#000000", "sub_shadow": 0.0,
                         "sub_bg_enable": True, "sub_bg_color": "#000000", "sub_bg_opacity": 55.0, "use_box": True,
                         "box_x": 0.08, "box_y": 0.85, "box_w": 0.84, "box_h": 0.08, "follow_hardsub": True,
                         "text_effect": TEXT_EFFECTS[0],
                         "bg_style": "Hộp mờ bo góc", "karaoke": False},
            "tts": {"enable_tts": True, "tts_source": TTS_SOURCES[0], "soft_timing": True, "natural_sync": False,
                    "tts_speed": 1.0, "max_tempo": 1.35, "multi_voice": True, "speaker_count": "Tự động"},
            "blur": {"auto_detect": True},
            "trim": {"enable": False, "head": "0", "tail": "0", "segments": []},
            "color": dict(_COLOR_OFF),
        }),
    Template(
        name="⚡ Reup nhanh không lời — chống trùng mạnh",
        description="Cho video không thoại (động vật, DIY, thư giãn…): cắt 1 giây đầu/cuối, dọc 9:16 cắt vừa khung, "
                    "lật gương, phóng 110%, tăng tốc 110%, chỉnh màu + hạt phim + vignette, bật đủ chống trùng. "
                    "Không phụ đề, không lồng tiếng.",
        badge="FAST", builtin=True, patch=True,
        parts=["video", "subtitle", "tts", "blur", "trim", "color"],
        sections={
            "video": {"ratio": "9:16 (Dọc)", "fit_mode": FIT_MODES[0], "resolution": "1080p", "fps": "30", "zoom": 110,
                      "speed": 110, "flip_h": True, "mute": False, "keep_original_audio": True, "vocal_filter": False,
                      "demucs_enable": False,
                      "original_volume": 100.0, "adaptive_ducking": True, "normalize_audio": True,
                      "output_suffix": "_reup", "loop_to_voice": False},
            "subtitle": {"enable_subtitle": False, "translate_enable": False},
            "tts": {"enable_tts": False, "multi_voice": False},
            "blur": {"auto_detect": False},
            "trim": {"enable": True, "head": "1", "tail": "1", "segments": []},
            "color": {**_COLOR_OFF, "enable": True, "brightness": 0.02, "contrast": 1.06, "saturation": 1.15,
                      "gamma": 1.0, "temperature": 6.0, "sharpen": 0.4, "grain": 8.0, "vignette": 18.0,
                      "dup_shift": True, "dup_noise": True, "dup_color": True, "dup_zoompan": True, "dup_audio": True,
                      "dup_gop": True},
        }),
    Template(
        name="↺ Mặc định (không chỉnh gì)",
        description="Đưa khung hình, phụ đề, giọng, che mờ, nhạc, logo, cắt, màu về mặc định. Giữ hàng đợi và cài đặt máy.",
        badge="0", builtin=True, patch=False, parts=list(TEMPLATE_SECTIONS), sections={}),
]


# ── Lưu / đọc ─────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    return "".join(c for c in name if c not in '\\/:*?"<>|').strip()[:80] or "mau"


def _migrate_old_presets() -> None:
    """Preset cũ (presets/*.json — cấu hình đầy đủ) → mẫu (mọi phần); bản gốc cất vào presets/_old."""
    if not PRESETS_DIR.exists():
        return
    for p in PRESETS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or "video" not in data or "sections" in data:
            continue                                     # không phải preset (vd. story_competitors.json)
        sections = {k: data[k] for k in TEMPLATE_SECTIONS if isinstance(data.get(k), dict)}
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        dst = TEMPLATES_DIR / f"{_safe(p.stem)}.json"
        if not dst.exists():
            dst.write_text(json.dumps({"name": p.stem, "parts": list(sections), "sections": sections,
                                       "description": "Chuyển từ preset cũ"}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        old = PRESETS_DIR / "_old"
        old.mkdir(exist_ok=True)
        shutil.move(str(p), str(old / p.name))
        logger.info("Đã chuyển preset cũ “%s” thành mẫu", p.stem)


def list_templates() -> list[Template]:
    try:
        _migrate_old_presets()
    except Exception:
        logger.warning("Không chuyển được preset cũ", exc_info=True)
    out = list(BUILTIN)
    if TEMPLATES_DIR.exists():
        for p in sorted(TEMPLATES_DIR.glob("*.json"), key=lambda f: f.stem.lower()):
            t = _read(p)
            if t is not None:
                out.append(t)
    return out


def _read(path: Path) -> Template | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sections = {k: v for k, v in (data.get("sections") or {}).items() if k in TEMPLATE_SECTIONS and isinstance(v, dict)}
        parts = [p for p in data.get("parts") or list(sections) if p in sections]
        thumb = path.with_suffix(".jpg")
        return Template(name=str(data.get("name") or path.stem), sections=sections, parts=parts,
                        description=str(data.get("description") or ""), thumb=thumb if thumb.exists() else None)
    except Exception:
        logger.warning("Mẫu hỏng: %s", path, exc_info=True)
        return None


def get_template(name: str) -> Template | None:
    return next((t for t in list_templates() if t.name == name), None)


def is_builtin(name: str) -> bool:
    return any(t.name == name for t in BUILTIN)


def save_template(state: AppState, name: str, parts: list[str], description: str = "", thumb=None) -> Template:
    """Lưu các phần ``parts`` của cấu hình đang hiển thị thành mẫu ``name`` (ghi đè mẫu cùng tên của bạn)."""
    if is_builtin(name):
        raise ValueError("Trùng tên mẫu dựng sẵn — đặt tên khác.")
    parts = [p for p in parts if p in TEMPLATE_SECTIONS]
    if not parts:
        raise ValueError("Chọn ít nhất một phần để lưu.")
    cur = state.sections_dict()
    sections = {p: copy.deepcopy(cur[p]) for p in parts}
    if "trim" in sections:
        sections["trim"]["segments"] = []               # đoạn cắt giữa video là của riêng từng video
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMPLATES_DIR / f"{_safe(name)}.json"
    path.write_text(json.dumps({"name": name, "parts": parts, "sections": sections, "description": description},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    jpg = path.with_suffix(".jpg")
    if thumb is not None:
        try:
            im = thumb.convert("RGB")
            im.thumbnail((320, 320))
            im.save(jpg, "JPEG", quality=85)
        except Exception:
            logger.warning("Không lưu được ảnh mẫu", exc_info=True)
    return _read(path) or Template(name=name, sections=sections, parts=parts, description=description)


def voice_summary(tts: dict) -> str:
    """Giọng lưu trong phần lồng tiếng của mẫu, viết cho người đọc: giọng chính + danh sách giọng theo thứ tự người nói."""
    import re

    from app.core.state import TTSConfig
    from app.services.tts.generator import voice_id_for

    def short(name: str) -> str:
        n = re.sub(r"^[a-z]{2,3}-[A-Z]{2}-", "", name or "").replace("MultilingualNeural", "").replace("Neural", "")
        n = re.sub(r"\s*\(.*?\)\s*$", "", n.split(" — ")[0].split(" - ")[0])
        return n.strip() or "—"

    if not tts.get("enable_tts", True):
        return "Lồng tiếng: tắt"
    try:
        cfg = TTSConfig(**{k: v for k, v in tts.items() if k in TTSConfig.__dataclass_fields__})
        main = short(cfg.eleven_voice_name if cfg.tts_provider == "ElevenLabs" and cfg.eleven_voice_name
                     else voice_id_for(cfg))
        prov = cfg.tts_provider.split(" (")[0]
    except Exception:
        main, prov = "—", ""
    out = f"Giọng chính: {main} ({prov})"
    if not tts.get("multi_voice"):
        return out + " — một người đọc hết."
    seq = []
    for sv in tts.get("speaker_voices") or []:
        if isinstance(sv, dict) and sv.get("voice_id"):
            p = str(sv.get("provider") or "").split(" (")[0]
            seq.append(f"Người {len(seq) + 1} → {short(str(sv.get('voice_name') or sv['voice_id']))}"
                       + (f" ({p})" if p != prov else ""))
    if not seq:
        return out + "\nMỗi người nói tự chọn một giọng Nam/Nữ."
    return out + "\n" + " · ".join(seq)


def delete_template(name: str) -> None:
    if is_builtin(name):
        raise ValueError("Không xoá được mẫu dựng sẵn.")
    path = TEMPLATES_DIR / f"{_safe(name)}.json"
    for p in (path, path.with_suffix(".jpg")):
        p.unlink(missing_ok=True)


def export_template(t: Template, dst: Path) -> None:
    data: dict[str, Any] = {"edit_studio_template": 1, "name": t.name, "parts": t.parts, "sections": t.sections,
                            "description": t.description, "patch": t.patch}
    if t.thumb is not None and t.thumb.exists():
        data["thumb_jpg"] = base64.b64encode(t.thumb.read_bytes()).decode()
    Path(dst).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def import_template(src: Path) -> Template:
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("File không phải mẫu Edit Studio.")
    if "sections" not in data and "video" in data:          # file cấu hình JSON cũ → mẫu đủ mọi phần
        data = {"name": Path(src).stem, "sections": {k: data[k] for k in TEMPLATE_SECTIONS if isinstance(data.get(k), dict)}}
    sections = {k: v for k, v in (data.get("sections") or {}).items() if k in TEMPLATE_SECTIONS and isinstance(v, dict)}
    if not sections:
        raise ValueError("File mẫu không có thiết lập nào.")
    name = str(data.get("name") or Path(src).stem)
    if is_builtin(name):
        name += " (nhập)"
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMPLATES_DIR / f"{_safe(name)}.json"
    path.write_text(json.dumps({"name": name, "parts": [p for p in data.get("parts") or list(sections) if p in sections],
                                "sections": sections, "description": data.get("description", "")},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    if data.get("thumb_jpg"):
        try:
            path.with_suffix(".jpg").write_bytes(base64.b64decode(data["thumb_jpg"]))
        except Exception:
            pass
    return _read(path)


# ── Áp mẫu ────────────────────────────────────────────────────────────────

def resolve(t: Template, base: dict[str, dict]) -> dict[str, dict]:
    """Các phần cấu hình sau khi áp ``t`` lên ``base`` (chỉ những phần trong ``t.parts``)."""
    from dataclasses import asdict

    from app.core.state import _SECTION_CLASSES

    out: dict[str, dict] = {}
    for part in t.parts:
        if part not in TEMPLATE_SECTIONS:
            continue
        cur = copy.deepcopy(base.get(part) or {})
        if t.patch:
            new = dict(cur)
            new.update(copy.deepcopy(t.sections.get(part) or {}))
        elif t.sections.get(part) is not None:
            new = copy.deepcopy(t.sections[part])
        else:                                           # mẫu «Mặc định»
            new = asdict(_SECTION_CLASSES[part]())
        for f in KEEP_FIELDS.get(part, ()):
            if f in cur:
                new[f] = cur[f]
        if part == "trim" and not t.patch:
            new["segments"] = cur.get("segments", [])    # mẫu không xoá đoạn cắt riêng đã chọn
        out[part] = new
    return out


def apply_shared(state: AppState, t: Template) -> None:
    """Áp mẫu vào cấu hình chung (mọi video chưa chỉnh riêng)."""
    act = state.active
    if act is not None and act.config is not None and state._shared is not None:
        state._shared.update(resolve(t, state._shared))      # đang hiển thị cấu hình riêng → sửa bản cất
    else:
        state.apply_sections(resolve(t, state.sections_dict()))
    state.template_name = t.name
    state.emit("config_loaded")
    state.mark_dirty()


def apply_to_item(state: AppState, item: VideoItem, t: Template) -> None:
    """Áp mẫu riêng cho một video (video đó có cấu hình riêng từ đây)."""
    base = state.effective_sections(item)
    state.make_own(item, {**base, **resolve(t, base)}, template=t.name)
    item.template = t.name


def differs(state: AppState, t: Template | None) -> bool:
    """Cấu hình chung đã bị chỉnh khác mẫu đang dùng?"""
    if t is None:
        return False
    base = state.shared_sections()
    want = resolve(t, base)
    return any(base.get(k) != v for k, v in want.items())


def thumbnail_for(t: Template, size: int = 96):
    """Ảnh đại diện mẫu: ảnh đã lưu; mẫu dựng sẵn / không có ảnh → ô màu kèm nhãn."""
    from PIL import Image, ImageDraw, ImageFont

    if t.thumb is not None and t.thumb.exists():
        try:
            im = Image.open(t.thumb).convert("RGB")
            im.thumbnail((size, size))
            canvas = Image.new("RGB", (size, size), (24, 24, 28))
            canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
            return canvas
        except Exception:
            pass
    palette = {"9:16": (217, 70, 239), "16:9": (59, 130, 246), "FAST": (234, 179, 8), "0": (82, 82, 91)}
    col = palette.get(t.badge, (34, 197, 94))
    im = Image.new("RGB", (size, size), tuple(int(c * 0.35) for c in col))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 6, size - 6, size - 6), radius=10, outline=col, width=3)
    text = t.badge or (t.name.strip()[:2].upper() if t.name else "?")
    try:
        font = ImageFont.load_default(size=int(size * 0.26))
    except TypeError:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font)
    d.text(((size - (bbox[2] - bbox[0])) / 2, (size - (bbox[3] - bbox[1])) / 2 - bbox[1]), text, fill=col, font=font)
    return im

