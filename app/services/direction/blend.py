"""Pha trộn vị trí kim bánh xe cảm xúc → lời dặn cách đọc. Thuần tính toán, không phụ thuộc engine.

``blend(theta, level)``  : góc kim (độ, 0 = đỉnh, thuận chiều kim đồng hồ) + bậc 1–5 → cảm xúc chính + 4 sắc thái (0–100).
``compile_direction(d)`` : một ``direction`` (giọng + cảm xúc + kiểu nói + yêu cầu riêng) → ``Compiled``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data"
_SHARP = 6          # độ nhọn của búp cos(d)^k: lớn hơn = sắc thái lân cận hẹp hơn
_TOP = 4            # số sắc thái giữ lại (1 chính + 3 lân cận) — khớp 4 thanh sắc thái trên UI


@lru_cache(maxsize=1)
def emotions() -> list[dict]:
    return json.loads((_DATA / "emotions.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def speaking_styles() -> list[dict]:
    return json.loads((_DATA / "speaking_styles.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def emotions_map() -> dict[str, dict]:
    return {e["id"]: e for e in emotions()}


@lru_cache(maxsize=1)
def _styles_map() -> dict[str, dict]:
    return {s["id"]: s for s in speaking_styles()}


def _ang_dist(a: float, b: float) -> float:
    """Góc lệch nhỏ nhất giữa hai hướng (độ, 0–180)."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def blend(theta: float, level: int, overrides: dict[str, int] | None = None) -> tuple[str, dict[str, int]]:
    """Kim ở ``theta`` độ, bậc ``level`` (1–5) → (id cảm xúc chính, {id: cường độ 0–100} cho top-4).

    Cảm xúc chính ≈ ``level × 20`` (bậc 5 = 100); lân cận giảm theo cos(góc lệch)^k. ``overrides`` (người dùng kéo
    tay thanh sắc thái) ghi đè cường độ theo id."""
    level = max(1, min(5, int(level)))
    raw = [(e["id"], max(0.0, math.cos(math.radians(_ang_dist(theta, e["angle"])))) ** _SHARP) for e in emotions()]
    raw.sort(key=lambda x: x[1], reverse=True)
    top = raw[:_TOP]
    main_id, main_w = top[0]
    scale = (level * 20) / main_w if main_w > 0 else 0.0
    out = {eid: int(round(min(100.0, w * scale))) for eid, w in top}
    for k, v in (overrides or {}).items():
        out[k] = max(0, min(100, int(v)))
    return main_id, out


@dataclass
class Compiled:
    style_en: str               # lời dặn cho Gemini TTS (đưa vào khung [Lời dặn])
    description_vi: str          # mô tả tiếng Việt để hiển thị cho người dùng
    temperature: float           # độ biến thiên biểu cảm cho Gemini
    edge_rate: int               # % tốc độ cho Edge TTS (−… chậm, +… nhanh)
    edge_pitch: int              # Hz / % cao độ cho Edge TTS
    main_id: str                 # id cảm xúc chính
    weights: dict[str, int]      # cường độ 4 sắc thái (để UI vẽ lại thanh)


def _weighted(weights: dict[str, int], field: str) -> float:
    em = emotions_map()
    tot = sum(weights.values())
    return sum(em[i][field] * w for i, w in weights.items()) / tot if tot else 0.0


def _strip_like(s: str) -> str:
    return s[5:] if s.startswith("like ") else s


def _style_phrase(sa: str | None, sb: str | None, mix: float) -> str:
    sm = _styles_map()
    a = sm.get(sa or "")
    if not a:
        return ""
    b = sm.get(sb or "")
    if b and mix and mix > 0.15:
        return f"delivered {a['style_en']}, with a touch of {_strip_like(b['style_en'])}"
    return f"delivered {a['style_en']}"


def _vi_style_phrase(sa: str | None, sb: str | None, mix: float) -> str:
    sm = _styles_map()
    a = sm.get(sa or "")
    if not a:
        return ""
    b = sm.get(sb or "")
    if b and mix and mix > 0.15:
        return f"theo kiểu {a['vi']} pha chút {b['vi']}"
    return f"theo kiểu {a['vi']}"


def compile_direction(direction: dict, *, use_vi_style: bool = False) -> Compiled:
    """``direction`` (xem schema mục 5 của spec) → lời dặn + tham số. ``use_vi_style`` để A/B lời dặn Việt vs Anh."""
    emo = direction.get("emotion") or {}
    theta = float(emo.get("theta", 0.0))
    level = max(1, min(5, int(emo.get("level", 3))))
    main_id, weights = blend(theta, level, emo.get("overrides"))
    em = emotions_map()
    main = em[main_id]
    ordered = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    sp = direction.get("speaking_style") or {}
    special = (direction.get("special") or "").strip()

    # Lời dặn cho Gemini phải NGẮN (≤ ~25 từ) kẻo giọng trôi → chỉ nêu tối đa 2 sắc thái đủ mạnh (≥ 40);
    # sắc thái yếu («a hint of») chỉ để hiển thị tiếng Việt, không nhét vào prompt
    sec_en = []
    for eid, w in ordered:
        if eid == main_id or w < 40 or len(sec_en) >= 2:
            continue
        noun = em[eid]["noun_en"]
        sec_en.append(f"strongly {noun}" if w >= 70 else f"noticeably {noun}")
    e_avg = _weighted(weights, "energy")
    v_avg = _weighted(weights, "valence")
    energy_en = "speaking rapidly, raised pitch" if e_avg >= 0.6 else \
        "speaking slowly, soft and low" if e_avg <= 0.35 else ""

    if use_vi_style:
        sec_vi = []
        for eid, w in ordered:
            if eid == main_id or w < 15:
                continue
            tag = "đậm" if w >= 70 else "rõ" if w >= 40 else "thoáng"
            sec_vi.append(f"{tag} nét {em[eid]['vi'].lower()}")
        clauses = [f"Đọc giọng {main['levels_vi'][level - 1]}", *sec_vi,
                   _vi_style_phrase(sp.get("a"), sp.get("b"), sp.get("mix", 0.0))]
        style = ", ".join(c for c in clauses if c)
    else:
        clauses = [", ".join([main["levels_en"][level - 1], *sec_en]),
                   _style_phrase(sp.get("a"), sp.get("b"), sp.get("mix", 0.0)), energy_en]
        style = ", ".join(c for c in clauses if c)
    if special:
        style = f"{style}. {special}"

    temperature = direction.get("temperature")
    if temperature is None:
        temperature = round(0.7 + (level - 1) * 0.15, 2)          # bậc 1 = 0,70 … bậc 5 = 1,30
    edge_rate = int(max(-40, min(50, round((e_avg - 0.45) * 90))))
    edge_pitch = int(max(-40, min(40, round(v_avg * 22 + (e_avg - 0.45) * 28))))

    main_vi = main["levels_vi"][level - 1]
    sec_disp = [f"{em[eid]['vi'].lower()} {w}%" for eid, w in ordered if eid != main_id and w >= 15]
    desc = f"{main['vi']} — «{main_vi}» ({main['desc_vi']})"
    if sec_disp:
        desc += " · pha: " + ", ".join(sec_disp)
    return Compiled(style, desc, float(temperature), edge_rate, edge_pitch, main_id, weights)
