"""Ghi log ra file + console; đồng thời cho UI đăng ký nhận từng dòng."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler

from app.config.paths import LOGS_DIR

_FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_ui_sinks: list[Callable[[str, str], None]] = []


class _UIHandler(logging.Handler):
    """Đẩy log tới các sink của UI (log panel). Sink phải tự nhảy về UI thread."""

    def emit(self, record: logging.LogRecord) -> None:
        if not _ui_sinks:
            return
        try:
            msg = self.format(record)
        except Exception:
            return
        for sink in list(_ui_sinks):
            try:
                sink(record.levelname, msg)
            except Exception:
                pass


def setup_logging(level: int = logging.INFO) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    fh = RotatingFileHandler(LOGS_DIR / "edit_studio.log", maxBytes=2_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)

    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(_FMT))
        root.addHandler(sh)

    uh = _UIHandler(logging.WARNING)      # UI chỉ hiện cảnh báo/lỗi → khỏi định dạng mọi dòng INFO
    uh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    root.addHandler(uh)

    # Thư viện bên thứ ba khá ồn ào — chỉ giữ cảnh báo trở lên.
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "PIL", "asyncio", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def add_ui_sink(sink: Callable[[str, str], None]) -> None:
    _ui_sinks.append(sink)


def remove_ui_sink(sink: Callable[[str, str], None]) -> None:
    try:
        _ui_sinks.remove(sink)
    except ValueError:
        pass
