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


def _count(path: str | None, target: str = "") -> tuple[int, str, int]:
    """(số câu, câu đầu, số câu còn sót chữ Hán / Kana / Hangul khi dịch sang ngôn ngữ không dùng các chữ đó)."""
    if not path or not Path(path).exists():
        return 0, "", 0
    try:
        cues = load_srt(path)
    except Exception:
        return 0, "", 0
    first = next((c.plain for c in cues if c.plain.strip()), "")
    if target:
        from app.services.translate_router import _leaks

        return len(cues), first, sum(1 for c in cues if _leaks(c.plain, target))
    return len(cues), first, 0


_LATIN = re.compile(r"(?<![A-Za-zÀ-ỹ])[A-Za-z][A-Za-z0-9]{2,}(?![A-Za-zÀ-ỹ])")
_VI_MARK = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)
#: chữ Latin viết như tiếng Việt không dấu hay gặp — không tính là từ nước ngoài
_VI_PLAIN = {"anh", "em", "con", "cho", "nhung", "va", "khi", "nha", "minh", "ban", "toi", "the", "thi", "mot", "hai",
             "ba", "bon", "nam", "sau", "bay", "tam", "chin", "muoi", "ok", "okay", "video", "clip", "app", "web", "online",
             "game", "live", "show", "team", "fan", "idol", "top", "hot", "like", "share"}


def _foreign_words(path: str | None) -> list[str]:
    """Từ Latin (tên hãng, tên người…) trong bản dịch tiếng Việt chưa có trong từ điển phát âm — giọng Việt hay đọc
    sai các từ này."""
    if not path or not Path(path).exists():
        return []
    try:
        from app.services.tts.normalize import load_pronunciations

        known, _pat = load_pronunciations()
        text = " ".join(c.plain for c in load_srt(path))
    except Exception:                                    # noqa: BLE001
        return []
    out: dict[str, None] = {}
    for w in _LATIN.findall(_VI_MARK.sub(" ", text)):
        low = w.lower()
        if low in _VI_PLAIN or low in known or any(low.startswith(k) for k in known):
            continue
        out.setdefault(w, None)
    return list(out)


def _voice_issues(pipe, item: VideoItem) -> list[str]:
    """Vấn đề của track giọng: giọng cũ (đổi giọng / tốc độ / bản dịch sau khi chuẩn bị), câu bỏ trống, câu tràn khung."""
    import json

    out = []
    try:
        voice, fresh, _trim = pipe.current_voice_track(item)
    except Exception:                                    # noqa: BLE001
        return out
    if voice is None:
        return out
    if not fresh:
        out.append("giọng cũ — cấu hình đã đổi, Chuẩn bị lại để tạo giọng mới")
    try:
        data = json.loads(Path(voice).with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if data.get("failed"):
        out.append(f"{data['failed']} câu không đọc được (bỏ trống)")
    if data.get("fallback"):
        out.append(f"{data['fallback']} câu đọc tạm bằng Edge (VieNeu lỗi)")
    return out          # câu đọc chưa đạt / tràn khung: từng câu ở dòng «Câu cần nghe lại»


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
    n_orig, first_orig, _ = _count(item.srt_path)
    n_tr, first_tr, leaks = _count(item.srt_translated_path, s.translate_target)
    if not uses_text:
        rows.append(Row("srt", "doc", "Lời thoại", "Không dùng (mẫu không phụ đề / lồng tiếng)", OFF))
    elif not n_orig:
        rows.append(Row("srt", "doc", "Lời thoại", "Chưa có — bấm «Chuẩn bị» (hoặc nhận diện ở tab Nhận diện)", WARN))
    else:
        val = f"{n_orig} câu"
        level = OK
        if n_tr:
            val += f" · đã dịch sang {s.translate_target}"
            if leaks:                                    # giọng đọc bỏ qua chữ Hán, phụ đề vẫn hiện → nhắc sửa
                val += f" · {leaks} câu còn sót chữ gốc"
                level = WARN
        elif need_tr:
            val += " · CHƯA DỊCH"
            level = WARN
        detail = f"«{_clip(first_tr or first_orig)}»"
        if t.enable_tts and t.tts_provider.startswith("VieNeu") and n_tr and s.translate_target == "Tiếng Việt":
            foreign = _foreign_words(item.srt_translated_path)
            if foreign:
                detail += (f"\nTừ nước ngoài: {', '.join(foreign[:6])}{'…' if len(foreign) > 6 else ''} — đọc sai thì "
                           "thêm cách đọc ở Cài đặt › AI trên máy › «Từ điển phát âm»")
        rows.append(Row("srt", "doc", "Lời thoại", val, level, detail))

    # 3. Phụ đề trên video
    if s.enable_subtitle:
        where = "đặt đè lên chỗ chữ cứng" if s.follow_hardsub else "vị trí cố định"
        from app.core.state import SRT_MODE_LABELS

        rows.append(Row("subtitle", "subtitles", "Phụ đề trên video",
                        f"{SRT_MODE_LABELS.get(s.srt_mode, s.srt_mode)} · chữ {s.sub_size}px · {where}"))
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
                from app.services.diarize import assign_voices, guess_gender

                pick = {sv["name"]: sv for sv in assign_voices(smap, t.male_voices, t.female_voices)}
                parts = []
                for nm in names[:6]:
                    info = smap.speakers.get(nm)
                    sv = pick.get(nm) or {}
                    voice = sv.get("voice_name") or _short_voice(sv.get("voice_id", ""))
                    g = f" ({info.gender})" if info and info.gender else ""
                    auto = "tự chọn giọng " + ({"Nam": "nam", "Nữ": "nữ"}.get(guess_gender(info), "") if info else "")
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
        issues = _voice_issues(pipe, item) if has_voice else []
        if issues:                                       # ghép vào dòng vừa thêm: nói rõ giọng đang có vấn đề gì
            last = rows[-1]
            rows[-1] = Row(last.module, last.icon, last.title, last.value + " · " + " · ".join(issues), WARN, last.detail)
    else:
        rows.append(Row("tts", "speaker", "Người nói & lồng tiếng", "Không lồng tiếng", OFF))

    # 4b. Câu cần nghe lại: giọng đọc sai, đọc nhanh / tràn khung, chưa dịch, lệch xưng hô so với hồ sơ kịch bản
    if uses_text and n_orig:
        from app.services.line_qa import QA_TITLE, summary

        try:
            flags = pipe.review_flags(item)
        except Exception:                                # noqa: BLE001 — soát lỗi hỏng không được làm hỏng bảng Duyệt
            flags = {}
        if flags:
            first = sorted(flags)[:8]
            rows.append(Row("srt", "warning", QA_TITLE, summary(flags), WARN,
                            "Câu " + ", ".join(str(i + 1) for i in first) + (" …" if len(flags) > 8 else "")
                            + " — «Sửa ›» chỉ hiện các câu này ở tab Lời thoại: ▶ nghe, sửa, rồi icon loa tạo lại giọng."))

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
    good = [cl for cl in (b.clips or []) if isinstance(cl, dict) and cl.get("path")]
    clips = [Path(str(cl["path"])).name for cl in good]
    gone = [Path(str(cl["path"])).name for cl in good if not Path(str(cl["path"])).exists()]
    if b.enable and clips:
        vols = sorted({f"{float(cl.get('volume', 100)) * b.master_volume / 100:.0f}%" for cl in good})
        rows.append(Row("bgm", "music", "Nhạc nền", f"{', '.join(_clip(n, 28) for n in clips[:3])}"
                        + (f" +{len(clips) - 3}" if len(clips) > 3 else "") + f" · {', '.join(vols[:3])}"
                        + (" · tự hạ khi có lời" if b.ducking else "")
                        + (f" · KHÔNG THẤY FILE: {', '.join(gone[:2])}" if gone else ""), WARN if gone else OK))
    else:
        rows.append(Row("bgm", "music", "Nhạc nền", "Không ghép nhạc", OFF))

    # 8. Logo & chữ
    parts = []
    missing = []
    if br.logo_enable and br.logo_path:
        parts.append(f"logo {Path(br.logo_path).name} ({br.logo_position.lower()})")
        if not Path(br.logo_path).exists():
            missing.append(Path(br.logo_path).name)
    if br.text_enable and br.text.strip():
        parts.append(f"chữ «{_clip(br.text, 24)}»")
    if br.watermark_enable and br.watermark_text.strip():
        parts.append(f"watermark «{_clip(br.watermark_text, 20)}»")
    if br.frame_enable:
        parts.append("viền khung")
    if missing:                                          # file bị xoá / dời sau khi lưu mẫu → bản xuất thiếu logo
        parts.append(f"KHÔNG THẤY FILE: {', '.join(missing)}")
    rows.append(Row("brand", "logo", "Logo & chữ", " · ".join(parts) if parts else "Không gắn",
                    WARN if missing else (OK if parts else OFF)))

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

    # 10. Hiệu ứng & chống trùng (chống trùng chạy cả khi công tắc chỉnh màu / hiệu ứng tắt)
    dup = sum(bool(x) for x in (c.dup_shift, c.dup_noise, c.dup_color, c.dup_zoompan, c.dup_audio, c.dup_gop))
    if c.enable:
        parts = []
        if any(abs(x - d) > 1e-3 for x, d in ((c.brightness, 0.0), (c.contrast, 1.0), (c.saturation, 1.0),
                                               (c.gamma, 1.0), (c.temperature, 0.0))):
            parts.append("chỉnh màu")
        if dup:
            parts.append(f"chống trùng ×{dup}")
        if c.grain:
            parts.append("hạt phim")
        if c.vignette:
            parts.append("vignette")
        tex_gone = c.texture_enable and getattr(c, "texture_path", "") and not Path(c.texture_path).exists()
        if c.texture_enable:
            parts.append("texture" + (" — KHÔNG THẤY FILE" if tex_gone else ""))
        rows.append(Row("color", "color", "Hiệu ứng & chống trùng", " · ".join(parts) or "Bật (chưa chỉnh gì)",
                        WARN if tex_gone else OK))
    elif dup:
        rows.append(Row("color", "color", "Hiệu ứng & chống trùng", f"chống trùng ×{dup}"))
    else:
        rows.append(Row("color", "color", "Hiệu ứng & chống trùng", "Không", OFF))
    return rows
