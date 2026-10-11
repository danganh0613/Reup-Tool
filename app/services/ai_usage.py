"""Đếm lượt dùng AI dịch trong ngày (Gemini / DeepSeek) để hiện «37/500 lượt · 7 %».

Google không có API hỏi «còn bao nhiêu lượt» → máy tự đếm mỗi lượt gọi thành công, so với hạn mức ngày của gói miễn
phí. Lượt dùng ở nơi khác (AI Studio, app khác, máy khác) không đếm được; Google báo hết lượt ngày (429 PerDay) thì đánh
dấu hết ngay. Hạn mức Gemini đặt lại lúc nửa đêm giờ Mỹ (Pacific) ≈ 14–15 giờ chiều giờ Việt Nam; DeepSeek trả phí, không
có hạn mức ngày → đếm lượt + token, kèm số dư lần hỏi gần nhất.

Lưu ở cache/ai_usage.json (tên model + số lượt, KHÔNG lưu key) — app và chạy dòng lệnh dùng chung: giao diện đọc lại
khi file đổi. Mọi lỗi ghi / đọc chỉ ghi log — đếm lượt không bao giờ làm hỏng việc dịch.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app.config.paths import CACHE_DIR, LOGS_DIR

logger = logging.getLogger(__name__)

FILE = CACHE_DIR / "ai_usage.json"

#: hạn mức lượt / ngày của gói miễn phí theo tên model (AI Studio › Rate limit, 10/2026) — khớp mẫu đầu tiên;
#: None = không giới hạn / chưa rõ
FREE_PER_DAY: tuple[tuple[str, int | None], ...] = (
    (r"gemma", 14400),
    (r"live|native-audio|image|imagen|veo", None),
    (r"-tts", 10),
    (r"transcribe", 25),
    (r"2\.5-flash-lite", 20),
    (r"flash-lite", 500),
    (r"flash", 20),
)

_lock = threading.Lock()
_data: dict = {}
_mtime: float | None = None                      # mtime file lúc đọc / ghi gần nhất (khác → nạp lại)


def limit_of(provider: str, model: str) -> int | None:
    if provider != "Gemini":
        return None
    for pat, n in FREE_PER_DAY:
        if re.search(pat, model or ""):
            return n
    return None


def _pacific(now: datetime | None = None) -> tuple[datetime, int]:
    """(giờ Mỹ Pacific, độ lệch giờ so với UTC) — tự tính giờ mùa hè (Chủ nhật thứ 2 tháng 3 → Chủ nhật đầu tháng 11,
    lúc 2 giờ sáng), khỏi cần dữ liệu múi giờ (bản cài trên Windows không có sẵn)."""
    utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def sunday(month: int, nth: int) -> date:
        d = date(utc.year, month, 1)
        return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (nth - 1))

    summer = (datetime.combine(sunday(3, 2), time(10), timezone.utc) <= utc
              < datetime.combine(sunday(11, 1), time(9), timezone.utc))
    off = -7 if summer else -8
    return utc + timedelta(hours=off), off


def day_of(provider: str, now: datetime | None = None) -> str:
    """Ngày tính lượt: Gemini theo giờ Mỹ (Google đặt lại hạn mức nửa đêm Pacific), DeepSeek theo giờ máy."""
    if provider == "Gemini":
        return _pacific(now)[0].date().isoformat()
    return (now or datetime.now(timezone.utc)).astimezone().date().isoformat()


def day_start(provider: str, now: datetime | None = None) -> datetime:
    """Lúc bắt đầu ngày tính lượt, theo giờ máy (không kèm múi giờ)."""
    if provider != "Gemini":
        return datetime.combine((now or datetime.now(timezone.utc)).astimezone().date(), time())
    p, off = _pacific(now)
    return (datetime.combine(p.date(), time(), timezone(timedelta(hours=off)))
            .astimezone().replace(tzinfo=None))


def reset_at(provider: str = "Gemini", now: datetime | None = None) -> str:
    """Giờ máy lúc hạn mức đặt lại, vd «14:00»."""
    return (day_start(provider, now) + timedelta(days=1)).strftime("%H:%M")


def _load() -> None:
    """Nạp lại file khi nó đổi (vd chạy dòng lệnh vừa dịch). Chưa có file → ước tính lượt đã gọi hôm nay từ nhật ký."""
    global _data, _mtime
    try:
        m = FILE.stat().st_mtime
    except (OSError, ValueError):
        if _mtime is None:
            _data, _mtime = _from_log(), 0.0
        return
    if m == _mtime:
        return
    try:
        _data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("Đọc %s lỗi", FILE, exc_info=True)
    _mtime = m


def _save() -> None:
    global _mtime
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(FILE)
        _mtime = FILE.stat().st_mtime
    except (OSError, ValueError):
        logger.debug("Ghi %s lỗi", FILE, exc_info=True)


def _today(provider: str) -> dict:
    """Mục của nhà cung cấp cho ngày tính lượt hiện tại (sang ngày mới thì về 0, giữ số dư)."""
    p = _data.setdefault(provider, {})
    day = day_of(provider)
    if p.get("day") != day:
        p.update(day=day, models={})
    return p


def _from_log() -> dict:
    """Lần đầu (chưa có file đếm): đếm dòng «Gọi Gemini/model» trong nhật ký từ đầu ngày tính lượt — ước tính (tính cả
    lượt bị từ chối), đỡ hơn bắt đầu từ 0 khi hôm nay đã dịch."""
    data: dict = {}
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\| Gọi (Gemini|DeepSeek)/(\S+) \(")
    for path in (LOGS_DIR / "edit_studio.log.1", LOGS_DIR / "edit_studio.log"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = pat.match(line)
            if not m or datetime.fromisoformat(m.group(1)) < day_start(m.group(2)):
                continue
            p = data.setdefault(m.group(2), {"day": day_of(m.group(2)), "models": {}})
            u = p["models"].setdefault(m.group(3), {"calls": 0})
            u["calls"] += 1
    return data


def _change(fn) -> None:
    """Sửa bộ đếm rồi ghi file — lỗi gì cũng chỉ ghi log (gọi từ luồng dịch: đếm lượt không được làm hỏng việc dịch)."""
    with _lock:
        try:
            _load()
            fn()
            _save()
        except Exception:                              # noqa: BLE001
            logger.debug("Bộ đếm lượt AI lỗi", exc_info=True)


def record(provider: str, model: str, tokens: int = 0) -> None:
    """Một lượt gọi thành công."""
    def add() -> None:
        u = _today(provider)["models"].setdefault(model, {"calls": 0})
        u["calls"] = u.get("calls", 0) + 1
        if tokens:
            u["tokens"] = u.get("tokens", 0) + tokens

    _change(add)


def mark_full(provider: str, model: str) -> None:
    """Máy chủ báo hết lượt ngày của model → thanh lên 100 % tới lúc đặt lại."""
    _change(lambda: _today(provider)["models"].setdefault(model, {"calls": 0}).update(full=True))


def set_balance(provider: str, text: str) -> None:
    now = datetime.now()
    _change(lambda: _data.setdefault(provider, {}).update(balance=text, balance_at=now.strftime("%H:%M"),
                                                          balance_ts=now.timestamp()))


@dataclass(frozen=True)
class Usage:
    provider: str
    model: str
    calls: int = 0
    limit: int | None = None
    full: bool = False
    tokens: int = 0
    balance: str = ""
    balance_at: str = ""
    balance_ts: float = 0.0
    reset: str = ""

    @property
    def share(self) -> float:
        """Phần đã dùng (0–1); hết lượt → 1."""
        if self.full:
            return 1.0
        return min(1.0, self.calls / self.limit) if self.limit else 0.0


def usage(provider: str, model: str) -> Usage:
    with _lock:
        _load()
        p = _data.get(provider) or {}
        u = (p.get("models") or {}).get(model, {}) if p.get("day") == day_of(provider) else {}
        return Usage(provider, model, int(u.get("calls", 0)), limit_of(provider, model), bool(u.get("full")),
                     int(u.get("tokens", 0)), p.get("balance", ""), p.get("balance_at", ""),
                     float(p.get("balance_ts", 0.0)), reset_at(provider) if provider == "Gemini" else "")
