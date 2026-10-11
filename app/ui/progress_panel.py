"""Thanh trạng thái dưới cùng — MỘT dòng, đọc là hiểu ngay:

    ⟳  Đang chuẩn bị 1/3 · phim.mp4   Khử giọng gốc · bước 5/6        ▬▬▬▬░░ 42%  còn ~1:20          [log]
    ✓  Chuẩn bị xong 3 video           Duyệt từng video ở tab «Duyệt»                          [Mở tab Duyệt] [log]
    ✖  1 video lỗi                     phim.mp4: Gemini quá tải (503)                          [Thử lại] [log]

Trái → phải: trạng thái (biểu tượng + câu chính) → việc đang làm (chữ mờ) → tiến độ (một thanh, một con số,
thời gian còn lại) → việc nên bấm khi xong (Thử lại / Mở thư mục) → log. Nút Dừng ở header. Các bước hiển thị là bước thật của
video đó (bỏ bước không cần: đã có SRT, không lồng tiếng…), lấy từ thông điệp của pipeline.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button

if TYPE_CHECKING:
    from app.core.state import VideoItem
    from app.ui.main_window import MainWindow

from app.services.job_runner import STEP_ORDER as _ORDER, STEPS, step_key  # noqa: E402

STEP_LABEL = dict(STEPS)
SPIN = "◐◓◑◒"


def planned_steps(window: MainWindow, item: VideoItem, mode: str) -> list[str]:
    """Các bước video này sẽ đi qua — cùng kế hoạch JobRunner dùng để chia thanh tiến độ."""
    return window.pipeline_for(item).planned_steps(item, mode)


def _clock(sec: float) -> str:
    sec = max(0, int(sec + 0.5))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|Volumes|tmp)/)[^\s:'\"«»]+")


def friendly_error(message: str, item: VideoItem | None = None) -> str:
    """Câu lỗi cho người dùng: bỏ đường dẫn dài, dịch các lỗi kỹ thuật hay gặp sang lời dễ hiểu."""
    m = " ".join((message or "").splitlines()[:1])
    if item is not None:
        m = m.replace(item.path, item.name)
    from app.services.qwen_asr import MISSING_MARK
    from app.services.translate_router import UNAVAILABLE_MARK

    for mark in (UNAVAILABLE_MARK, MISSING_MARK):   # câu báo đã dễ hiểu, kèm nút «Chọn model…» / «Cài…»
        if mark in m:
            return m.replace(mark, "").strip()
    # đường dẫn dài → chỉ tên file (số trong tên file kiểu VID_20240403 không bị hiểu nhầm là mã lỗi 403)
    m = _PATH.sub(lambda mt: Path(mt.group(0)).name, m)
    low = m.lower()
    if "ffprobe" in low or "invalid data" in low or "moov atom" in low:
        return "file hỏng hoặc không phải video — mở thử bằng trình phát khác"
    if re.search(r"\b(503|529)\b", low) or "overloaded" in low or "quá tải" in low:
        return "máy chủ AI đang quá tải — đợi một lát rồi bấm Thử lại"
    if "chưa có gemini api key" in low:
        return "chưa nhập API key Gemini (Cài đặt › AI dùng API key)"
    if "kết nối" in low and "gemini" in low:
        return "không kết nối được tới Gemini — kiểm tra mạng rồi bấm Thử lại"
    if "api key" in low or re.search(r"\b(401|403)\b", low):
        return "API key chưa đúng hoặc hết hạn — kiểm tra ở Cài đặt › AI dùng API key"
    if re.search(r"no space left|errno 28|disk full|not enough space|ổ đĩa đầy|ổ đầy", low):
        return "ổ đĩa đầy — giải phóng dung lượng rồi thử lại"
    if re.search(r"out of memory|cuda error|cudnn_status|cublas_status|gpu hết bộ nhớ", low):
        return "card đồ hoạ hết bộ nhớ — đóng game / ứng dụng nặng rồi bấm Thử lại"
    if re.search(r"connectionerror|max retries exceeded|getaddrinfo|name resolution|failed to establish|"
                 r"read timed out|connecttimeout|sslerror|mất mạng", low):
        return "mất mạng / không tải được — kiểm tra Internet rồi bấm Thử lại"
    if low.startswith("ffmpeg lỗi:"):
        return m.split(":", 1)[1].split(" (mã ")[0].strip()
    return m or "không rõ lý do — xem log"


def short_path(path: str) -> str:
    parts = Path(path).parts
    return path if len(parts) <= 3 else "…/" + "/".join(parts[-2:])


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


class ProgressPanel(ctk.CTkFrame):
    def __init__(self, master, window: MainWindow) -> None:
        super().__init__(master, fg_color=T.BG_PANEL, corner_radius=10, height=46)
        self.window = window
        self.grid_columnconfigure(2, weight=1)
        self.icon = ctk.CTkLabel(self, text="•", width=22, font=T.font(16, "bold"), text_color=T.TEXT_MUTED)
        self.icon.grid(row=0, column=0, padx=(14, 6), pady=7)
        # ``status``: câu chính (in đậm) — giữ tên cũ cho các chỗ đọc trạng thái
        self.status = ctk.CTkLabel(self, text="", font=T.font(13, "bold"), text_color=T.TEXT, anchor="w")
        self.status.grid(row=0, column=1, sticky="w")
        self.detail = ctk.CTkLabel(self, text="", font=T.font(12), text_color=T.TEXT_DIM, anchor="w")
        self.detail.grid(row=0, column=2, sticky="ew", padx=(12, 12))
        self.bar = ctk.CTkProgressBar(self, width=220, height=6, progress_color=T.ACCENT)
        self.bar.set(0)
        self.percent = ctk.CTkLabel(self, text="", width=40, font=T.font(12, "bold"), text_color=T.TEXT, anchor="e")
        self.eta = ctk.CTkLabel(self, text="", font=T.font(12), text_color=T.TEXT_MUTED, anchor="w")
        self.action_btn: ctk.CTkButton | None = None
        self.log_btn = button(self, "", icon_name="doc", size="sm", variant="ghost", command=window.open_log,
                              tip="Xem log chi tiết")
        self.log_btn.grid(row=0, column=8, padx=(4, 10))

        self._mode = "idle"
        self._spin_id: str | None = None
        self._spin_i = 0
        self._t0 = 0.0
        self._steps: dict[str, list[str]] = {}
        self._waiting = ""
        self._run_title = ""
        self._p = 0.0
        self._p_at = 0.0
        self._shown = 0.0
        self._stopping = False          # đã bấm Dừng: giữ dòng «Đang dừng…», không để bước đang chạy ghi đè
        self.set_idle()

    # ── Khung hiển thị ───────────────────────────────────────────────────
    def _show(self, mode: str, icon: str, color: str, title: str, detail: str = "") -> None:
        self._mode = mode
        self.icon.configure(text=icon, text_color=color)
        self.status.configure(text=title)
        self.detail.configure(text=_clip(detail + self._waiting, 120))
        if mode == "running":
            self._start_spin()
        else:
            self._stop_spin()

    def _meter(self, on: bool) -> None:
        for w, col, kw in ((self.bar, 3, {"padx": (0, 8)}), (self.percent, 4, {}), (self.eta, 5, {"padx": (8, 4)})):
            if on:
                w.grid(row=0, column=col, **kw)
            else:
                w.grid_remove()

    def _action(self, label: str | None, command: Callable[[], None] | None = None, icon_name: str | None = "refresh",
                primary: bool = True) -> None:
        """Đúng một nút «việc nên làm tiếp» (Thử lại / Mở thư mục / Mở tab Duyệt) — hoặc không có."""
        if self.action_btn is not None:
            self.action_btn.destroy()
            self.action_btn = None
        if label:
            self.action_btn = button(self, label, icon_name=icon_name, size="sm",
                                     variant="primary" if primary else "secondary", command=command)
            self.action_btn.grid(row=0, column=6, padx=(8, 0))

    def _start_spin(self) -> None:
        if self._spin_id is None:
            self._spin()

    def _spin(self) -> None:
        self._spin_i = (self._spin_i + 1) % len(SPIN)
        self.icon.configure(text=SPIN[self._spin_i])
        if self._spin_i == 0:
            self._update_eta()                          # thời gian còn lại chạy đều cả khi một bước lâu chưa báo tiến độ
        self._spin_id = self.after(140, self._spin)

    def _stop_spin(self) -> None:
        if self._spin_id is not None:
            try:
                self.after_cancel(self._spin_id)
            except Exception:
                pass
            self._spin_id = None

    def destroy(self) -> None:
        self._stop_spin()
        super().destroy()

    # ── Trạng thái ───────────────────────────────────────────────────────
    def set_idle(self) -> None:
        n = len(self.window.app_state.videos)
        if n:
            label = self.window.render_btn.cget("text").strip() if hasattr(self.window, "render_btn") else "Xuất video"
            if label == "Mở Duyệt":                   # không còn gì để chạy — chỉ còn xem / xuất lại
                detail = (f"{n} video đã chuẩn bị / xuất — muốn xuất lại video nào: mở tab Duyệt, sửa rồi bấm «Duyệt»")
            else:
                detail = f"{n} video trong hàng đợi — bấm «{label}» trên cùng để bắt đầu"
        else:
            detail = "Nạp video vào hàng đợi để bắt đầu"
        self._show("idle", "•", T.TEXT_MUTED, "Sẵn sàng", detail)
        self._meter(False)
        self._action(None)

    def refresh_idle(self) -> None:
        if self._mode == "idle":
            self.set_idle()

    def start_queue(self, items: list[VideoItem], mode: str, on_skip: Callable[[], None] | None = None) -> None:
        """``on_skip``: nhiều video → nút «Bỏ qua video này» (bỏ video đang chạy, các video sau chạy tiếp)."""
        self._t0 = time.time()
        self._stopping = False
        self._steps = {it.path: planned_steps(self.window, it, mode) for it in items}
        self._run_title = "Đang chuẩn bị" if mode == "prepare" else "Đang xuất"
        self._show("running", SPIN[0], T.ACCENT, f"{self._run_title} {len(items)} video", "Bắt đầu…")
        self._meter(True)
        self._action("Bỏ qua video này", on_skip, None, False) if on_skip else self._action(None)
        self.set_progress(0.0, reset=True)

    def stopping(self) -> None:
        """Đã bấm Dừng: báo ngay (bước đang chạy có thể cần vài giây mới dừng được)."""
        if self._mode != "running":
            return
        self._stopping = True
        self.status.configure(text="Đang dừng…")
        self.detail.configure(text="Chờ bước đang chạy dừng hẳn (vài giây)")
        self._action(None)

    def update_item(self, it: VideoItem, index: int, total: int, overall: float) -> None:
        if self._stopping:                           # bước đang chạy còn báo tiến độ sau khi bấm Dừng → giữ «Đang dừng…»
            return
        steps = self._steps.setdefault(it.path, ["read"])
        key = step_key(it.message)
        if key and key not in steps:                 # bước phát sinh (vd. cần nhận diện người nói) → chèn đúng thứ tự
            steps.append(key)
            steps.sort(key=_ORDER.index)
        what = STEP_LABEL.get(key, it.message) if key else it.message
        if key:
            what += f" · bước {steps.index(key) + 1}/{len(steps)}"
        head = f"{self._run_title} {index}/{total}" if total > 1 else self._run_title
        self._show("running", self.icon.cget("text"), T.ACCENT, f"{head} · {_clip(it.name, 36)}", what)
        self.set_progress(overall)

    def start_task(self, name: str, detail: str = "Đang chạy…") -> None:
        self._t0 = time.time()
        self._stopping = False
        self._show("running", SPIN[0], T.ACCENT, name, detail)
        self._meter(True)
        self._action(None)
        self.set_progress(0.0, reset=True)

    def set_detail(self, text: str) -> None:
        """Việc đang làm của tác vụ lẻ (vd từng bước cài Qwen) — thay chữ «Đang chạy…» đứng yên cả chục phút."""
        if self._mode == "running" and not self._stopping:
            self.detail.configure(text=_clip(text + self._waiting, 120))

    def set_progress(self, p: float, reset: bool = False) -> None:
        p = max(0.0, min(1.0, p))
        if self._mode == "running" and not reset:
            p = max(p, self._shown)                  # trong một lượt thanh chỉ đi lên (bước sau tính lại không kéo lùi)
        self._shown = p
        self.bar.set(p)
        self.percent.configure(text=f"{int(p * 100)}%")
        if p > self._p or p == 0:
            self._p, self._p_at = p, time.time()
        self._update_eta()

    def _update_eta(self) -> None:
        """Còn ~m:ss = (thời gian đã chạy / phần đã xong) × phần còn lại, trừ đi lúc đã trôi từ lần báo tiến độ cuối."""
        p, now = self._p, time.time()
        el = self._p_at - self._t0 if self._t0 else 0.0
        if self._mode == "running" and p >= 0.05 and el >= 8:      # chờ đủ số liệu, đỡ nhảy lung tung
            left = el * (1 - p) / p - (now - self._p_at)
            self.eta.configure(text=f"còn ~{_clock(left)}" if left >= 5 else "sắp xong")
        else:
            self.eta.configure(text="")

    def set_waiting(self, names: list[str]) -> None:
        self._waiting = f"  ·  chờ sau: {', '.join(names)}" if names else ""
        cur = self.detail.cget("text").split("  ·  chờ sau:")[0]
        self.detail.configure(text=_clip(cur + self._waiting, 120))

    def result(self, kind: str, title: str, detail: str = "", action: tuple | None = None) -> None:
        """Kết quả cuối: ``kind`` ok / error / stopped. ``action`` = (chữ, lệnh, icon, primary?) — việc nên bấm tiếp."""
        icon, color = {"ok": ("✓", T.SUCCESS), "error": ("✖", T.DANGER), "stopped": ("■", T.WARNING)}[kind]
        self._t0 = 0.0
        self._stopping = False
        self._show(kind, icon, color, title, detail)
        self._meter(False)
        if action:
            self._action(*action)
        else:
            self._action(None)

    def notice(self, title: str, detail: str = "") -> None:
        """Ghi chú nhẹ (xem trước, giọng cũ…) — không đè lên việc đang chạy."""
        if self._mode == "running":
            return
        if self.action_btn is not None and self._mode in ("error", "stopped"):
            return                                     # đang có lỗi + nút (Thử lại, Chọn model…) → giữ nguyên
        self._show("notice", "i", T.INFO, title, detail)
        self._meter(False)
        self._action(None)

    def flash(self, text: str, ms: int = 3000) -> None:
        """Nhắc ngắn ở dòng chi tiết (vd «Bấm Esc lần nữa…») rồi trả lại chữ cũ."""
        old = self.detail.cget("text")
        self.detail.configure(text=text)
        self.after(ms, lambda: self.detail.cget("text") == text and self.detail.configure(text=old))

    def set_status(self, text: str) -> None:
        self.notice(text)

    def reset(self, text: str = "") -> None:
        self.set_idle()
