"""Gợi ý cảm xúc tự động cho từng câu (spec 4.5): dịch xong, gọi AI THÊM MỘT LƯỢT (lô ≤ 60 câu, Groq ≤ 30) để
chọn cảm xúc / bậc / kiểu nói trong 2 bảng có sẵn. Lưu file kèm SRT đã dịch: ``<srt>.directions.json``.

Tách khỏi lượt dịch để không làm đổi chất lượng bản dịch và không đẩy token ra của Groq vượt giới hạn mỗi phút.
Người dùng chỉ cần chỉnh lại trên bánh xe, không phải đặt tay từ đầu."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from app.core.state import Settings
from app.services.direction.blend import emotions, emotions_map, speaking_styles

logger = logging.getLogger(__name__)

VERSION = 1
_BATCH = 60
_GROQ_BATCH = 30                 # Groq gói miễn phí: ~1000 token ra mỗi phút
_TOKENS_PER_LINE = 30            # {"id": 12, "emotion": "vui_tuoi", "level": 3, "style": "quang_cao"}


def sidecar_path(srt_path: str | Path) -> Path:
    return Path(str(srt_path) + ".directions.json")


def load(srt_path: str | Path | None) -> dict[int, dict]:
    """{chỉ số cue (0-based): direction} — không có file / file hỏng → {}."""
    if not srt_path:
        return {}
    try:
        data = json.loads(sidecar_path(srt_path).read_text(encoding="utf-8"))
        return {int(k): v for k, v in (data.get("items") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save(srt_path: str | Path, items: dict[int, dict]) -> Path:
    p = sidecar_path(srt_path)
    p.write_text(json.dumps({"version": VERSION, "items": {str(k): v for k, v in sorted(items.items())}},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def direction_for(emotion_id: str, level: int, style_id: str) -> dict:
    """Direction mặc định từ gợi ý (giọng để trống = dùng giọng của video)."""
    em = emotions_map()
    e = em.get(emotion_id) or em["tram_lang"]
    return {"voice_id": None, "emotion": {"theta": e["angle"], "level": max(1, min(5, int(level))), "overrides": {}},
            "speaking_style": {"a": style_id, "b": "", "mix": 0.0}, "special": "", "temperature": None}


def _prompt(rows: list[tuple[int, str, str]]) -> str:
    emo = ", ".join(f"{e['id']} ({e['vi']})" for e in emotions())
    sty = ", ".join(f"{s['id']} ({s['vi']})" for s in speaking_styles())
    lines = "\n".join(f"{i}. SOURCE: {src}\n   VI: {vi}" for i, src, vi in rows)
    return (
        "You direct the Vietnamese voice-over of a short video (product review / affiliate style). For EACH numbered "
        "line pick how it should be voiced.\n"
        f"- emotion: exactly one id from: {emo}\n"
        "- level: 1 (subtle) … 5 (extreme); most lines 2–3, use 4–5 only for real peaks\n"
        f"- style: exactly one id from: {sty}. Keep the SAME style for most of the video unless the scene clearly "
        "changes.\n"
        "Return ONLY JSON: {\"items\": [{\"id\": <id>, \"emotion\": \"<id>\", \"level\": <1-5>, \"style\": \"<id>\"}]} "
        "with one item per line.\n\nLines:\n" + lines)


def suggest(src: list[str], vi: list[str], *, settings: Settings, log: Callable[[str], None] | None = None,
            cancel=None) -> dict[int, dict]:
    """Gợi ý direction cho từng câu. Lỗi một lô → bỏ qua lô đó (câu đó dùng hướng mặc định), không làm hỏng việc dịch."""
    from app.services.translate_router import Router, _load_json

    em, st = emotions_map(), {s["id"] for s in speaking_styles()}
    step = _GROQ_BATCH if settings.translate_provider == "Groq" else _BATCH
    router = Router(settings, log)
    out: dict[int, dict] = {}
    idxs = [i for i in range(min(len(src), len(vi))) if (vi[i] or "").strip()]
    for k in range(0, len(idxs), step):
        if cancel is not None and cancel.is_set():
            break
        part = idxs[k:k + step]
        rows = [(i + 1, src[i].replace("\n", " "), vi[i].replace("\n", " ")) for i in part]
        try:
            data = _load_json(router.call(_prompt(rows), 80 + _TOKENS_PER_LINE * len(part)))
            items = data.get("items") if isinstance(data, dict) else None
        except Exception as exc:                       # noqa: BLE001 — gợi ý là phụ, không được làm hỏng bước dịch
            logger.warning("Gợi ý cảm xúc lỗi: %s", exc)
            items = None
        for it in items or []:
            try:
                i = int(it["id"]) - 1
            except (KeyError, TypeError, ValueError):
                continue
            if i not in part:
                continue
            e_id = it.get("emotion") if it.get("emotion") in em else "tram_lang"
            s_id = it.get("style") if it.get("style") in st else "tan_phiem"
            try:
                lv = int(it.get("level", 3))
            except (TypeError, ValueError):
                lv = 3
            out[i] = direction_for(e_id, lv, s_id)
    if log:
        log(f"Gợi ý cảm xúc: {len(out)}/{len(idxs)} câu (chỉnh lại được trên bánh xe Đạo diễn âm thanh)")
    return out
