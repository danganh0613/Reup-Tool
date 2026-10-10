"""Sinh file ASS từ danh sách cue + cấu hình kiểu chữ, để libass burn-in.

Mô hình vị trí: **khung phụ đề** (x, y, w, h theo tỉ lệ khung ra). Chữ căn giữa theo
chiều ngang, bám đáy khung, tự xuống dòng theo bề rộng khung (MarginL/R/V của ASS).
Nếu ``use_box`` tắt thì dùng chế độ vị trí cũ (Dưới/Giữa/Trên/Tuỳ chỉnh).

Hộp nền được vẽ bằng lớp Dialogue riêng (style ``Box``, chữ trong suốt, BorderStyle=4)
nằm dưới lớp chữ chính — vừa có nền vừa giữ được viền chữ. Karaoke dùng thẻ ``\\k``
với thời lượng chia theo độ dài từng từ.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.state import SUB_BG_STYLES, SUB_POSITIONS, TEXT_EFFECTS, SubtitleConfig
from app.services.srt_utils import Cue

_HTML_TAG = re.compile(r"</?([ibu])>", re.IGNORECASE)
_OTHER_TAG = re.compile(r"<[^>]+>")


def _ass_color(hex_color: str, opacity_pct: float) -> str:
    """#RRGGBB + độ mờ (0–100) → &HAABBGGRR."""
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    alpha = int(round((100 - max(0.0, min(100.0, opacity_pct))) * 2.55))
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str, uppercase: bool = False) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = _HTML_TAG.sub(lambda m: "{\\%s%s}" % (m.group(1).lower(), "0" if m.group(0).startswith("</") else "1"), text)
    text = _OTHER_TAG.sub("", text)
    if uppercase:
        text = text.upper()
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = re.sub(r"\\\{\\([ibu][01])\\\}", r"{\\\1}", text)   # khôi phục thẻ override đã tạo ở trên
    return text.replace("\n", "\\N")


#: chia thời lượng câu cho từng chữ: trọng số 2 + số ký tự, dấu câu thêm khoảng nghỉ — chỉ dùng khi chưa có mốc chữ
#: theo giọng thật (``word_timing``: căn ép đúng chữ / bám khoảng lặng)
_PAUSE = {",": 0.6, ";": 0.8, ":": 0.8, ".": 1.0, "?": 1.0, "!": 1.0, "…": 1.0}
POP_SCALE = 115                       # % phóng chữ đang đọc (kiểu CapCut)
POP_IN, POP_OUT = 70, 90              # ms chữ nổi lên / trở lại


def words_of(text: str) -> list[str]:
    """Các chữ của câu đúng như chữ nổi tách (bỏ thẻ, gộp dòng) — mốc chữ theo giọng tính trên cùng cách tách này."""
    plain = " ".join(_OTHER_TAG.sub("", text.replace("\n", " ")).split())
    return plain.split(" ") if plain else []


def word_spans(words: list[str], duration: float) -> list[tuple[float, float]]:
    """(bắt đầu, kết thúc) — giây tính từ đầu câu — của từng chữ khi câu đọc trong ``duration`` giây."""
    weights = [2.0 + len(re.sub(r"[^\w]", "", w)) for w in words]
    pauses = [4.0 * _PAUSE.get(w[-1:], 0.0) for w in words]
    total = (sum(weights) + sum(pauses[:-1])) or 1.0
    out: list[tuple[float, float]] = []
    t = 0.0
    for wt, ps in zip(weights, pauses):
        a = t / total * duration
        t += wt
        out.append((a, t / total * duration))
        t += ps
    return out


def _bgr(hex_color: str) -> str:
    """#RRGGBB → &HBBGGRR& (màu trong thẻ \\1c)."""
    return _ass_color(hex_color, 100.0)[:2] + _ass_color(hex_color, 100.0)[4:] + "&"


def _alpha(opacity_pct: float) -> str:
    return f"&H{int(round((100 - max(0.0, min(100.0, opacity_pct))) * 2.55)):02X}&"


def _anim_text(text: str, duration: float, cfg: SubtitleConfig, at: float | None = None, offset: float = 0.0,
               spans: list[tuple[float, float]] | None = None) -> str:
    """Câu với chữ đang đọc nổi lên theo ``cfg.karaoke_mode`` (pop · fill · reveal). ``at``: giây tính từ lúc câu
    hiện → vẽ đúng trạng thái lúc đó, không chuyển động (khung xem trước tĩnh). ``offset``: lời bắt đầu muộn hơn lúc
    câu hiện bấy nhiêu giây (câu ngắn được hiện sớm cho đủ 1 s); âm = câu đã đọc dở từ trước (phát xem trước giữa câu).
    ``spans``: (bắt đầu, kết thúc) từng chữ tính từ lúc bắt đầu đọc — mốc theo giọng thật; không có thì ước lượng."""
    words = words_of(text)
    if not words:
        return ""
    if cfg.sub_uppercase:
        words = [w.upper() for w in words]
    if not spans or len(spans) != len(words):
        spans = word_spans(words, max(0.05, duration))
    mode = cfg.karaoke_mode if cfg.karaoke_mode in ("pop", "fill", "reveal") else "pop"
    base, hl = _bgr(cfg.sub_color), _bgr(cfg.karaoke_color)
    a1, a4 = _alpha(cfg.sub_opacity), _alpha(min(cfg.sub_opacity, 70.0))
    big = f"\\fscx{POP_SCALE}\\fscy{POP_SCALE}"
    out = []
    for w, (s0, s1) in zip(words, spans):
        w = w.replace("{", "\\{").replace("}", "\\}")
        a, b = max(0, round((s0 + offset) * 1000)), max(0, round((s1 + offset) * 1000))
        if at is not None:                     # trạng thái tĩnh tại giây ``at`` của câu
            said, now = at >= s0 + offset, s0 + offset <= at < s1 + offset
            if mode == "pop":
                tag = f"\\1c{hl}{big}" if now else f"\\1c{base}\\fscx100\\fscy100"
            elif mode == "fill":
                tag = f"\\1c{hl if said else base}"
            else:
                tag = f"\\1a{a1}\\3a{a1}\\4a{a4}" if said else "\\1a&HFF&\\3a&HFF&\\4a&HFF&"
        elif mode == "pop":
            tag = (f"\\1c{base}\\fscx100\\fscy100\\t({a},{a + POP_IN},\\1c{hl}{big})"
                   f"\\t({b},{b + POP_OUT},\\1c{base}\\fscx100\\fscy100)")
        elif mode == "fill":
            tag = f"\\1c{base}\\t({a},{a + POP_IN},\\1c{hl})"
        else:
            tag = f"\\1a&HFF&\\3a&HFF&\\4a&HFF&\\t({a},{a + POP_IN},\\1a{a1}\\3a{a1}\\4a{a4})"
        out.append("{" + tag + "}" + w)
    return " ".join(out)


def _effect_params(cfg: SubtitleConfig, scale: float) -> tuple[float, float, str]:
    """(outline, shadow, extra override tags) theo hiệu ứng chữ đã chọn."""
    outline = round(cfg.sub_stroke_width * scale, 2)
    shadow = round(cfg.sub_shadow * scale, 2)
    extra = ""
    eff = cfg.text_effect
    if eff == TEXT_EFFECTS[1]:            # viền đậm
        outline = max(outline, round(3.0 * scale, 2))
    elif eff == TEXT_EFFECTS[2]:          # bóng đổ
        shadow = max(shadow, round(3.0 * scale, 2))
    elif eff == TEXT_EFFECTS[3]:          # viền + bóng
        outline = max(outline, round(2.0 * scale, 2))
        shadow = max(shadow, round(3.0 * scale, 2))
    elif eff == TEXT_EFFECTS[4]:          # glow
        outline = max(outline, round(4.0 * scale, 2))
        extra = f"\\blur{round(6 * scale, 1)}"
    return outline, shadow, extra


BOX_LINES = 2          # khung lưu trong cấu hình cao vừa 2 dòng chữ (câu dài hơn thì tràn lên trên, đáy giữ nguyên)


def box_height_px(cfg: SubtitleConfig, width: int, height: int, lines: int = BOX_LINES, size: float | None = None) -> float:
    """Chiều cao (px khung ra) của ``lines`` dòng chữ: libass cho mỗi dòng cao ≈ đúng cỡ chữ, cộng viền + bóng."""
    scale = min(width, height) / 1080.0
    fs = (cfg.sub_size if size is None else size) * scale
    outline, shadow, _ = _effect_params(cfg, scale)
    return max(1, lines) * fs + 2 * outline + shadow + 6 * scale


def fit_box_height(cfg: SubtitleConfig, width: int, height: int) -> None:
    """Cho khung cao vừa chữ theo cỡ hiện tại, giữ nguyên đáy (chữ bám đáy khung)."""
    h = min(0.9, box_height_px(cfg, width, height) / max(1, height))
    bottom = max(h, min(1.0, cfg.box_y + cfg.box_h))
    cfg.box_h = round(h, 4)
    cfg.box_y = round(max(0.0, bottom - h), 4)


def size_for_height(cfg: SubtitleConfig, width: int, height: int, h_px: float, lines: int = BOX_LINES) -> int:
    """Cỡ chữ để ``lines`` dòng cao vừa ``h_px`` (ngược của box_height_px)."""
    scale = min(width, height) / 1080.0
    outline, shadow, _ = _effect_params(cfg, scale)
    fs = (h_px - 2 * outline - shadow - 6 * scale) / max(1, lines)
    return int(round(max(12, min(120, fs / max(1e-6, scale)))))


def layout_margins(cfg: SubtitleConfig, width: int, height: int) -> tuple[int, int, int, int, int]:
    """(align, marginL, marginR, marginV, box_top_px) từ khung hoặc chế độ vị trí cũ."""
    scale = min(width, height) / 1080.0
    if cfg.use_box:
        x = max(0.0, min(0.95, cfg.box_x))
        w = max(0.05, min(1.0 - x, cfg.box_w))
        y = max(0.0, min(0.95, cfg.box_y))
        h = max(0.03, min(1.0 - y, cfg.box_h))
        ml = int(round(x * width))
        mr = int(round((1.0 - x - w) * width))
        mv = int(round((1.0 - y - h) * height))       # bám đáy khung
        top = (y + h) * height - box_height_px(cfg, width, height)   # nền dải ngang: cao vừa 2 dòng chữ
        return 2, max(0, ml), max(0, mr), max(0, mv), int(round(max(0.0, top)))
    side = int(round(width * (100 - max(20, min(100, cfg.sub_max_width))) / 200))
    margin_v = int(round(cfg.sub_margin_v * scale))
    pos_mode = cfg.sub_position
    if pos_mode == SUB_POSITIONS[1]:
        align = 5
    elif pos_mode == SUB_POSITIONS[2]:
        align = 8
    elif pos_mode == SUB_POSITIONS[3]:
        align = 5
    else:
        align = 2
    return align, side, side, margin_v, 0


def build_ass(cues: list[Cue], cfg: SubtitleConfig, width: int, height: int, anim_at: float | None = None) -> str:
    """``anim_at``: chữ nổi (karaoke) vẽ tĩnh đúng trạng thái tại giây này của câu — khung xem trước một khung hình."""
    width = max(16, int(width))
    height = max(16, int(height))
    scale = min(width, height) / 1080.0

    font_size = max(6, int(round(cfg.sub_size * scale)))
    outline, shadow, extra_tags = _effect_params(cfg, scale)
    spacing = round(cfg.sub_letter_spacing * scale, 2)
    align, ml, mr, mv, box_top = layout_margins(cfg, width, height)

    primary = _ass_color(cfg.sub_color, cfg.sub_opacity)
    secondary = "&H000000FF"
    outline_c = _ass_color(cfg.sub_stroke_color, cfg.sub_opacity)
    shadow_c = _ass_color("#000000", min(cfg.sub_opacity, 70))
    bg_style = cfg.bg_style if cfg.bg_style in SUB_BG_STYLES else SUB_BG_STYLES[0]
    bg_on = cfg.sub_bg_enable or bg_style != SUB_BG_STYLES[0]
    bg_color = "#000000" if bg_style == SUB_BG_STYLES[2] else cfg.sub_bg_color
    bg_opacity = 90.0 if bg_style == SUB_BG_STYLES[2] else cfg.sub_bg_opacity
    box_c = _ass_color(bg_color, bg_opacity)
    transparent = "&HFF000000"
    bold = -1 if cfg.sub_bold else 0
    italic = -1 if cfg.sub_italic else 0
    font = cfg.sub_font or "Arial"
    pad = max(2, int(round(8 * scale)))

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Main,{font},{font_size},{primary},{secondary},{outline_c},{shadow_c},{bold},{italic},0,0,"
        f"100,100,{spacing},0,1,{outline},{shadow},{align},{ml},{mr},{mv},1",
        f"Style: Box,{font},{font_size},{transparent},{transparent},{transparent},{box_c},{bold},{italic},0,0,"
        f"100,100,{spacing},0,4,{pad},0,{align},{ml},{mr},{mv},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    pos_tag = ""
    band = None                           # (đỉnh, đáy) px của dải nền ngang khi khối chữ căn giữa khung
    if cfg.use_box and cfg.box_center:
        # khối chữ (1 hay nhiều dòng) căn giữa khung theo chiều dọc; lề trái / phải vẫn giới hạn bề rộng dòng
        bx, bw = max(0.0, cfg.box_x), max(0.05, cfg.box_w)
        by, bh = max(0.0, cfg.box_y), max(0.03, cfg.box_h)
        pos_tag = f"{{\\an5\\pos({int(round((bx + bw / 2) * width))},{int(round((by + bh / 2) * height))})}}"
        band = (int(round(by * height)), int(round((by + bh) * height)))
    elif not cfg.use_box and cfg.sub_position == SUB_POSITIONS[3]:
        x = int(round(width * max(0, min(100, cfg.sub_x)) / 100))
        y = int(round(height * max(0, min(100, cfg.sub_y)) / 100))
        pos_tag = f"{{\\an5\\pos({x},{y})}}"
    if extra_tags:
        pos_tag += "{" + extra_tags + "}"
    if bg_style == SUB_BG_STYLES[4]:      # bóng mềm
        pos_tag += f"{{\\blur{round(3 * scale, 1)}\\shad{round(4 * scale, 1)}}}"

    sec_size = max(6, int(round(font_size * max(0.4, min(1.0, cfg.secondary_scale)))))

    for c in cues:
        if c.end <= c.start:
            continue
        plain = _ass_text(c.text, cfg.sub_uppercase)
        text = plain
        if cfg.karaoke:
            # mốc lời thật (trước khi câu ngắn được kéo dài cho đủ 1 s) — chữ nổi theo đúng nhịp đọc;
            # «words»: mốc từng chữ theo giọng (tỉ lệ trong đoạn giọng của câu)
            meta = c.meta if isinstance(c.meta, dict) else {}
            a0, dur = meta.get("anim", (c.start, c.duration))
            spans = [(u0 * dur, u1 * dur) for u0, u1 in meta["words"]] if meta.get("words") else None
            text = _anim_text(c.text, dur, cfg, anim_at, a0 - c.start, spans)
        if not text:
            continue
        secondary_text = c.meta.get("secondary") if isinstance(c.meta, dict) else None
        if secondary_text:
            text = f"{text}\\N{{\\fs{sec_size}}}{_ass_text(secondary_text, cfg.sub_uppercase)}"
        start, end = _ass_time(c.start), _ass_time(c.end)
        if bg_on and bg_style in (SUB_BG_STYLES[1], SUB_BG_STYLES[2]):
            lines.append(f"Dialogue: 0,{start},{end},Box,,0,0,0,,{pos_tag}{plain}")   # hộp nền: chữ tĩnh
        elif bg_on and bg_style == SUB_BG_STYLES[3]:
            # dải ngang toàn khung: vẽ hình chữ nhật bằng drawing mode ASS
            if band is not None:
                top, bottom = band
            else:
                top = box_top if cfg.use_box else height - mv - int(font_size * 2.2)
                bottom = height - mv if cfg.use_box else height - mv + int(font_size * 0.3)
            top, bottom = max(0, top), min(height, max(top + 4, bottom))
            lines.append(f"Dialogue: 0,{start},{end},Box,,0,0,0,,{{\\an7\\pos(0,0)\\1c&H{box_c[4:]}&\\1a&H{box_c[2:4]}&"
                         f"\\bord0\\shad0\\p1}}m 0 {top} l {width} {top} l {width} {bottom} l 0 {bottom}{{\\p0}}")
        lines.append(f"Dialogue: 1,{start},{end},Main,,0,0,0,,{pos_tag}{text}")

    return "\n".join(lines) + "\n"


def write_ass(cues: list[Cue], cfg: SubtitleConfig, width: int, height: int, path: str | Path,
              anim_at: float | None = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_ass(cues, cfg, width, height, anim_at), encoding="utf-8")
    return p
