"""Tóm tắt «video này sẽ ra sao» — mỗi dòng một tính năng, giá trị hiện tại + cảnh báo, kèm tab để sửa.

Dùng trong bảng Duyệt: nhìn một lượt biết video có bao nhiêu câu thoại, mấy người nói, ai lồng tiếng, nhạc gì, logo gì…
chỗ nào chưa ổn thì bấm «Sửa ›» mở đúng tab đó.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.state import SRT_MODES, TTS_SOURCES, VideoItem
from app.services.srt_utils import load_srt

OK, WARN, OFF = "ok", "warn", "off"


@dataclass
class Row:
    module: str          # tab để sửa
    icon: str
    title: str
    value: str
    level: str = OK
    detail: str = ""


def _short_voice(v: str) -> str:
    v = re.sub(r"^[a-z]{2,3}-[A-Z]{2}-", "", v or "")
    return v.replace("MultilingualNeural", " (đa ngữ)").replace("Neural", "").strip() or "—"


def _clip(s: str, n: int = 70) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _count(path: str | None) -> tuple[int, str]:
    if not path or not Path(path).exists():
        return 0, ""
    try:
        cues = load_srt(path)
    except Exception:
        return 0, ""
    first = next((c.plain for c in cues if c.plain.strip()), "")
    return len(cues), first


def summarize(window, item: VideoItem) -> list[Row]:
    st = window.app_state.state_for(item)
    pipe = window.pipeline_for(item)
    v, s, t, b, br, tr, c, bl = st.video, st.subtitle, st.tts, st.bgm, st.brand, st.trim, st.color, st.blur
    rows: list[Row] = []

    # 1. Khung hình
    ratio = "giữ tỉ lệ gốc" if v.ratio.startswith("Gốc") or v.ratio.startswith("Giữ") else v.ratio.split(" ")[0]
    bits = [ratio, v.fit_mode.lower(), v.resolution]
    if v.speed != 100:
        bits.append(f"tốc độ {v.speed}%")
    if v.zoom != 100:
        bits.append(f"phóng {v.zoom}%")
    if v.flip_h:
        bits.append("lật gương")
    rows.append(Row("video", "video", "Khung hình", " · ".join(bits)))

    # 2. Lời thoại
    uses_text = s.enable_subtitle or t.enable_tts
    need_tr = (s.enable_subtitle and s.srt_mode != SRT_MODES[0]) or (t.enable_tts and t.tts_source == TTS_SOURCES[0])
    n_orig, first_orig = _count(item.srt_path)
    n_tr, first_tr = _count(item.srt_translated_path)
    if not uses_text:
        rows.append(Row("srt", "doc", "Lời thoại", "Không dùng (mẫu không phụ đề / lồng tiếng)", OFF))
    elif not n_orig:
        rows.append(Row("srt", "doc", "Lời thoại", "Chưa có — bấm «Chuẩn bị» (hoặc nhận diện ở tab STT)", WARN))
    else:
        val = f"{n_orig} câu"
        level = OK
        if n_tr:
            val += f" · đã dịch sang {s.translate_target}"
        elif need_tr:
            val += " · CHƯA DỊCH"
            level = WARN
        rows.append(Row("srt", "doc", "Lời thoại", val, level, f"«{_clip(first_tr or first_orig)}»"))

    # 3. Phụ đề trên video
    if s.enable_subtitle:
        where = "đặt đè lên chỗ chữ cứng" if s.follow_hardsub else "vị trí cố định"
        rows.append(Row("subtitle", "subtitles", "Phụ đề trên video",
                        f"{s.srt_mode} · chữ {s.sub_size}px · {where}"))
    else:
        rows.append(Row("subtitle", "subtitles", "Phụ đề trên video", "Tắt — không hiện chữ", OFF))

    # 4. Người nói & lồng tiếng
    if t.enable_tts:
        has_voice = bool(item.voice_path and Path(item.voice_path).exists())
        if t.multi_voice:
            smap = pipe.speaker_map(item)
            if smap is None or not smap.speakers:
                rows.append(Row("tts", "speaker", "Người nói & lồng tiếng",
                                "Nhiều giọng — chưa nhận diện người nói (bấm «Chuẩn bị»)", WARN))
            else:
                names = smap.names()
                from app.services.diarize import resolve_voices

                pick = {sv["name"]: sv for sv in resolve_voices(t.speaker_voices, smap)}
                parts = []
                for nm in names[:6]:
                    info = smap.speakers.get(nm)
                    sv = pick.get(nm) or {}
                    voice = sv.get("voice_name") or _short_voice(sv.get("voice_id", ""))
                    g = f" ({info.gender})" if info and info.gender else ""
                    auto = "tự chọn giọng " + ({"Nam": "nam", "Nữ": "nữ"}.get(info.gender, "") if info else "")
                    parts.append(f"{nm}{g} → {voice if voice and voice != '—' else auto.strip()}")
                rows.append(Row("tts", "speaker", "Người nói & lồng tiếng",
                                f"{len(names)} người nói · mỗi người một giọng"
                                + ("" if has_voice else " · chưa tạo giọng"), OK if has_voice else WARN,
                                " · ".join(parts) + (" …" if len(names) > 6 else "")))
        else:
            from app.services.tts.generator import voice_id_for

            prov = t.tts_provider.split(" (")[0]
            rows.append(Row("tts", "speaker", "Người nói & lồng tiếng",
                            f"1 giọng đọc: {_short_voice(voice_id_for(t))} ({prov}) · tốc độ ×{t.tts_speed:g}"
                            + ("" if has_voice else " · chưa tạo giọng"), OK if has_voice else WARN))
    else:
        rows.append(Row("tts", "speaker", "Người nói & lồng tiếng", "Không lồng tiếng", OFF))

    # 5. Âm thanh gốc
    if v.mute:
        rows.append(Row("video", "speaker", "Âm thanh gốc", "Tắt toàn bộ âm thanh", OFF))
    elif not v.keep_original_audio:
        rows.append(Row("video", "speaker", "Âm thanh gốc", "Bỏ âm gốc" + (f" · lồng tiếng {v.tts_volume:.0f}%"
                                                                           if t.enable_tts else ""), OK))
    elif v.demucs_enable:
        from app.services.vocal_remove import cached_no_vocals

        done = cached_no_vocals(item.path) is not None
        rows.append(Row("video", "speaker", "Âm thanh gốc",
                        "Khử giọng gốc (AI) " + ("✓ đã khử" if done else "· sẽ khử khi chuẩn bị")
                        + f" · nền {v.original_volume:.0f}%" + (f" · lồng {v.tts_volume:.0f}%" if t.enable_tts else "")))
    else:
        loud = t.enable_tts and v.original_volume > 30
        rows.append(Row("video", "speaker", "Âm thanh gốc",
                        f"Giữ nguyên cả giọng gốc · {v.original_volume:.0f}%"
                        + (f" · lồng {v.tts_volume:.0f}%" if t.enable_tts else ""), WARN if loud else OK,
                        "Giọng gốc có thể lấn giọng lồng — bật «Khử giọng nói gốc (AI)»." if loud else ""))

    # 6. Che mờ
    parts = []
    level = OK
    if bl.auto_detect:
        from app.services.hardsub import cached_region

        reg = cached_region(item)
        if isinstance(reg, dict):
            parts.append(f"tự che chữ cứng ở {reg['y'] * 100:.0f}–{(reg['y'] + reg['h']) * 100:.0f}% chiều cao")
        elif reg is None:
            parts.append("tự dò: không thấy chữ cứng")
        else:
            parts.append("tự dò chữ cứng: chưa dò")
    if bl.enable and bl.regions:
        parts.append(f"{len(bl.regions)} vùng vẽ tay")
    rows.append(Row("blur", "blur", "Che mờ", " · ".join(parts) if parts else "Không che", level if parts else OFF))

    # 7. Nhạc nền
    clips = [Path(str(cl.get("path", ""))).name for cl in (b.clips or []) if isinstance(cl, dict) and cl.get("path")]
    if b.enable and clips:
        rows.append(Row("bgm", "music", "Nhạc nền", f"{', '.join(_clip(n, 28) for n in clips[:3])}"
                        + (f" +{len(clips) - 3}" if len(clips) > 3 else "") + f" · {b.master_volume:.0f}%"
                        + (" · tự hạ khi có lời" if b.ducking else "")))
    else:
        rows.append(Row("bgm", "music", "Nhạc nền", "Không ghép nhạc", OFF))

    # 8. Logo & chữ
    parts = []
    if br.logo_enable and br.logo_path:
        parts.append(f"logo {Path(br.logo_path).name} ({br.logo_position.lower()})")
    if br.text_enable and br.text.strip():
        parts.append(f"chữ «{_clip(br.text, 24)}»")
    if br.watermark_enable and br.watermark_text.strip():
        parts.append(f"watermark «{_clip(br.watermark_text, 20)}»")
    if br.frame_enable:
        parts.append("viền khung")
    rows.append(Row("brand", "logo", "Logo & chữ", " · ".join(parts) if parts else "Không gắn", OK if parts else OFF))

    # 9. Cắt
    if tr.enable:
        parts = []
        if str(tr.head).strip() not in ("", "0"):
            parts.append(f"bỏ {tr.head}s đầu")
        if str(tr.tail).strip() not in ("", "0"):
            parts.append(f"bỏ {tr.tail}s cuối")
        if tr.segments:
            parts.append(f"{len(tr.segments)} đoạn")
        rows.append(Row("trim", "cut", "Cắt", " · ".join(parts) or "Bật (chưa đặt mốc)", OK if parts else WARN))
    else:
        rows.append(Row("trim", "cut", "Cắt", "Giữ nguyên cả video", OFF))

    # 10. Hiệu ứng & chống trùng
    if c.enable:
        parts = []
        if any(abs(x - d) > 1e-3 for x, d in ((c.brightness, 0.0), (c.contrast, 1.0), (c.saturation, 1.0),
                                               (c.gamma, 1.0), (c.temperature, 0.0))):
            parts.append("chỉnh màu")
        dup = sum(bool(x) for x in (c.dup_shift, c.dup_noise, c.dup_color, c.dup_zoompan, c.dup_audio, c.dup_gop))
        if dup:
            parts.append(f"chống trùng ×{dup}")
        if c.grain:
            parts.append("hạt phim")
        if c.vignette:
            parts.append("vignette")
        if c.texture_enable:
            parts.append("texture")
        rows.append(Row("color", "color", "Hiệu ứng & chống trùng", " · ".join(parts) or "Bật (chưa chỉnh gì)"))
    else:
        rows.append(Row("color", "color", "Hiệu ứng & chống trùng", "Không", OFF))
    return rows
