"""Gợi ý cảm xúc tự động cho từng câu (spec 4.5): dịch xong, gọi AI THÊM MỘT LƯỢT (lô ≤ 60 câu, Groq ≤ 40) để
chọn cảm xúc / bậc cho từng câu + MỘT kiểu nói cho cả video. Lưu file kèm SRT đã dịch: ``<srt>.directions.json``.

Tách khỏi lượt dịch để không làm đổi chất lượng bản dịch và không đẩy token ra của Groq vượt giới hạn mỗi phút.
Người dùng chỉ cần chỉnh lại trên bánh xe, không phải đặt tay từ đầu.

- Trả về dạng mảng gọn ``[id, "cảm xúc", bậc]``: dạng object thụt lề tốn ~44 token / câu → lô 30 câu chạm trần
  max_tokens, mất 8 câu cuối mỗi lô (đo trên video mẫu).
- Kiểu nói chọn một lần ở lô đầu rồi giữ cho cả video (trước: mỗi lô tự chọn → Tán phiếm / Kể chuyện / Livestream
  nhảy giữa chừng cùng một người nói)."""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from app.core.state import Settings
from app.services.direction.blend import emotions, emotions_map, speaking_styles

logger = logging.getLogger(__name__)

VERSION = 1
_BATCH = 60
_GROQ_BATCH = 40                 # Groq gói miễn phí: ~1000 token ra mỗi phút
_TOKENS_PER_LINE = 20            # [12, "ngac_nhien", 3], — đo ~14–16 token
DEFAULT_STYLE = "tan_phiem"
#: số cặp (cảm xúc, bậc) tối đa cả video — mỗi cặp là một lời dặn riêng = một lượt đọc gộp riêng của Gemini TTS
#: (đo: không giới hạn → 15 cặp cho 77 câu, nhiều cặp chỉ 1 câu = 1 lượt gọi riêng; giọng kể cũng nhảy cảm xúc quá nhiều)
PALETTE = 6


def sidecar_path(srt_path: str | Path) -> Path:
    return Path(str(srt_path) + ".directions.json")


def _read(srt_path: str | Path | None) -> dict:
    if not srt_path:
        return {}
    try:
        data = json.loads(sidecar_path(srt_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load(srt_path: str | Path | None, n: int | None = None) -> dict[int, dict]:
    """{chỉ số cue (0-based): direction} — không có file / file hỏng → {}. ``n`` (số câu của SRT hiện tại) khác số câu
    lúc lưu → bản dịch đã đổi hẳn, direction cũ không còn khớp câu → {}."""
    data = _read(srt_path)
    if n is not None and isinstance(data.get("n"), int) and data["n"] != n:
        return {}
    try:
        return {int(k): v for k, v in (data.get("items") or {}).items() if isinstance(v, dict)}
    except (ValueError, AttributeError):
        return {}


def save(srt_path: str | Path, items: dict[int, dict], *, n: int | None = None, keep_manual: bool = False) -> Path:
    """Ghi file direction. ``keep_manual``: câu người dùng đã chỉnh tay trên bánh xe (``manual``) giữ nguyên khi gợi ý
    lại — nếu số câu không đổi."""
    if keep_manual:
        old = load(srt_path, n)
        items = {**items, **{k: v for k, v in old.items() if v.get("manual") and (n is None or k < n)}}
    data: dict = {"version": VERSION, "items": {str(k): v for k, v in sorted(items.items())}}
    if n is not None:
        data["n"] = n
    p = sidecar_path(srt_path)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def direction_for(emotion_id: str, level: int, style_id: str) -> dict:
    """Direction mặc định từ gợi ý (giọng để trống = dùng giọng của video)."""
    em = emotions_map()
    e = em.get(emotion_id) or em["tram_lang"]
    return {"voice_id": None, "emotion": {"theta": e["angle"], "level": max(1, min(5, int(level))), "overrides": {}},
            "speaking_style": {"a": style_id, "b": "", "mix": 0.0}, "special": "", "temperature": None}


def _prompt(rows: list[tuple[int, str, str]], style: str | None = None,
            used: list[tuple[str, int]] | None = None) -> str:
    emo = ", ".join(f"{e['id']} ({e['vi']})" for e in emotions())
    lines = "\n".join(f"{i}. SOURCE: {src}\n   VI: {vi}" for i, src, vi in rows)
    if style:
        rule, fmt = f"- speaking style of this video is already chosen ({style}); do not output it\n", ""
    else:
        sty = ", ".join(f"{s['id']} ({s['vi']})" for s in speaking_styles())
        rule, fmt = f"- style: ONE id for the whole video (same speaker throughout) from: {sty}\n", '"style": "<id>", '
    reuse = (" Already used in earlier lines — reuse these whenever they fit: "
             + ", ".join(f"{e} {lv}" for e, lv in used) + ".") if used else ""
    return (
        "You direct the Vietnamese voice-over of a short video (vlog / review / story). For EACH numbered line pick how "
        "it should be voiced.\n"
        f"- emotion: exactly one id from: {emo}\n"
        "- level: 1 (subtle) … 5 (extreme); most lines 2–3, use 4–5 only for real peaks\n"
        f"- keep a SMALL palette like a real narrator: at most {PALETTE} different (emotion, level) pairs for the whole "
        f"video; repeated / similar lines get the same pair.{reuse}\n"
        f"{rule}"
        f"Return ONLY compact JSON, one array per line, no indentation: "
        f"{{{fmt}\"items\": [[<line number>, \"<emotion id>\", <level 1-5>], …]}} with one array for EVERY line."
        "\n\nLines:\n" + lines)


def _int(x, default: int) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _row(it) -> tuple | None:
    """Một mục trả về → (số dòng, cảm xúc, bậc). Nhận cả mảng gọn lẫn object (model đôi khi không theo mẫu)."""
    if isinstance(it, dict):
        return it.get("id"), it.get("emotion"), it.get("level", 3)
    if isinstance(it, (list, tuple)) and len(it) >= 2:
        return it[0], it[1], it[2] if len(it) > 2 else 3
    return None


def suggest(src: list[str], vi: list[str], *, settings: Settings, log: Callable[[str], None] | None = None,
            cancel=None) -> dict[int, dict]:
    """Gợi ý direction cho từng câu. Lỗi một lô → bỏ qua lô đó (câu đó dùng hướng mặc định), không làm hỏng việc dịch."""
    from app.services.translate_router import Router, _load_json

    em, st = emotions_map(), {s["id"] for s in speaking_styles()}
    step = _GROQ_BATCH if settings.translate_provider == "Groq" else _BATCH
    router = Router(settings, log)
    got: dict[int, tuple[str, int]] = {}
    style: str | None = None
    idxs = [i for i in range(min(len(src), len(vi))) if (vi[i] or "").strip()]
    for k in range(0, len(idxs), step):
        part = idxs[k:k + step]
        for _attempt in range(2):                      # trả thiếu câu (bị cắt) → hỏi lại MỘT lần riêng các câu thiếu
            todo = [i for i in part if i not in got]
            if not todo or (cancel is not None and cancel.is_set()):
                break
            rows = [(i + 1, src[i].replace("\n", " "), vi[i].replace("\n", " ")) for i in todo]
            used = [p for p, _n in Counter(got.values()).most_common(PALETTE)]   # lô sau dùng lại bảng cảm xúc
            try:
                data = _load_json(router.call(_prompt(rows, style, used), 80 + _TOKENS_PER_LINE * len(todo)))
            except ValueError as exc:                  # JSON hỏng (bị cắt giữa chừng) → hỏi lại
                logger.warning("Gợi ý cảm xúc trả JSON hỏng: %s", exc)
                continue
            except Exception as exc:                   # noqa: BLE001 — gợi ý là phụ, không được làm hỏng bước dịch
                logger.warning("Gợi ý cảm xúc lỗi: %s", exc)
                break
            if not isinstance(data, dict):
                continue
            if style is None and isinstance(data.get("style"), str) and data["style"] in st:
                style = data["style"]
            for r in filter(None, map(_row, data.get("items") or [])):
                i = _int(r[0], 0) - 1
                if i in todo:
                    got[i] = (r[1] if isinstance(r[1], str) and r[1] in em else "tram_lang",
                              max(1, min(5, _int(r[2], 3))))
    out = {i: direction_for(e_id, lv, style or DEFAULT_STYLE) for i, (e_id, lv) in sorted(got.items())}
    if log:
        name = next((s["vi"] for s in speaking_styles() if s["id"] == (style or DEFAULT_STYLE)), "")
        log(f"Gợi ý cảm xúc: {len(out)}/{len(idxs)} câu · {len(set(got.values()))} kiểu cảm xúc · kiểu nói «{name}» "
            "(chỉnh lại được trên bánh xe Đạo diễn âm thanh)")
    return out
