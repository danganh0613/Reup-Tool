"""Thanh «lượt dùng AI hôm nay» của model đang chọn — Gemini: «37/500 · 7%» + thanh %; DeepSeek (trả phí, không giới
hạn lượt): số lượt, token và số dư. Tự cập nhật sau mỗi lượt gọi (cả lượt của chạy dòng lệnh): đọc lại bộ đếm
``ai_usage`` mỗi 1,5 giây — chỉ là một lần xem giờ sửa file, rất nhẹ."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import customtkinter as ctk

from app.config import theme as T
from app.services import ai_usage
from app.ui.widgets.common import hint
from app.ui.widgets.ui import Tooltip

TIP_GEMINI = ("Máy tự đếm mỗi lượt gọi Gemini trên máy này (dịch, tách câu, Nhờ AI sửa, Dựng truyện). Google không cho "
              "xem số lượt còn lại, nên lượt dùng ở nơi khác (AI Studio, app khác) không tính; Google báo hết lượt thì "
              "thanh lên 100%. Hạn mức gói miễn phí tính theo từng model, đặt lại lúc nửa đêm giờ Mỹ (≈ 14–15 giờ chiều).")
TIP_DEEPSEEK = ("DeepSeek trả phí theo lượng dùng, không giới hạn lượt mỗi ngày. Số dư hỏi DeepSeek sau khi dịch "
                "(không tốn tiền).")


def fetch_balance(key: str) -> None:
    """Hỏi số dư DeepSeek (luồng nền) — ``check_deepseek_key`` tự ghi số dư vào bộ đếm."""
    from app.services.translate_router import check_deepseek_key

    check_deepseek_key(key)


def _tokens(n: int) -> str:
    return f"{n / 1e6:.1f}M".replace(".", ",") if n >= 1_000_000 else f"{round(n / 1000)}K" if n >= 1000 else str(n)


class UsageBar(ctk.CTkFrame):
    TICK = 1500
    BALANCE_EVERY = 600                     # số dư cũ hơn 10 phút thì hỏi lại; vừa dịch xong thì hỏi ngay

    def __init__(self, master, target: Callable[[], tuple[str, str, str]]) -> None:
        """``target()`` → (nhà cung cấp, model, key DeepSeek — chỉ để hỏi số dư, không hiện)."""
        super().__init__(master, fg_color="transparent")
        self.target = target
        self.columnconfigure(0, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        self.title = ctk.CTkLabel(top, text="Lượt dùng hôm nay", font=T.font(12), text_color=T.TEXT_DIM, anchor="w")
        self.title.pack(side="left")
        icon = ctk.CTkLabel(top, text="ⓘ", font=T.font(13), text_color=T.TEXT_MUTED, width=16, cursor="hand2")
        icon.pack(side="left", padx=(4, 0))
        self.tip = Tooltip(icon, TIP_GEMINI, delay=150, wrap=60, click=True)
        self.count = ctk.CTkLabel(top, text="", font=T.font(12, "medium"), text_color=T.TEXT, anchor="e")
        self.count.pack(side="right")
        self.bar = ctk.CTkProgressBar(self, height=6, progress_color=T.ACCENT)
        self.bar.set(0)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(3, 2))
        self.note = hint(self, "")
        self.note.grid(row=2, column=0, sticky="ew")
        self._shown: tuple | None = None
        self._job: str | None = None
        self._asked = (-1, 0.0)             # (số lượt, lúc) lần hỏi số dư gần nhất
        self._asking = False
        self.refresh()
        self._job = self.after(self.TICK, self._tick)

    def refresh(self) -> None:
        provider, model, key = self.target()
        u = ai_usage.usage(provider, model)
        if (provider, model, u) != self._shown:
            self._shown = (provider, model, u)
            self._paint(u)
        if provider == "DeepSeek" and key:
            self._maybe_ask_balance(u, key)

    def _paint(self, u: ai_usage.Usage) -> None:
        color = T.DANGER if u.full or u.share >= 1 else T.WARNING if u.share >= 0.8 else T.ACCENT
        self.tip.text = TIP_DEEPSEEK if u.provider == "DeepSeek" else TIP_GEMINI
        if u.provider == "DeepSeek":
            self.bar.grid_remove()
            self.count.configure(text=f"{u.calls} lượt · {_tokens(u.tokens)} token", text_color=T.TEXT)
            money = (f"số dư {u.balance} (lúc {u.balance_at})" if u.balance and u.balance_at
                     else "trả phí theo lượng dùng")
            self.note.configure(text=f"{u.model} · {money}", text_color=T.TEXT_MUTED)
            return
        if not u.limit and not u.full:          # model chưa rõ hạn mức (Pro, model mới…) → chỉ đếm
            self.bar.grid_remove()
            self.count.configure(text=f"{u.calls} lượt", text_color=T.TEXT)
            self.note.configure(text=f"{u.model} · chưa rõ hạn mức ngày · đặt lại lúc {u.reset}",
                                text_color=T.TEXT_MUTED)
            return
        self.bar.grid()
        self.bar.configure(progress_color=color)
        self.bar.set(u.share)
        total = f"{u.calls}/{u.limit}" if u.limit else f"{u.calls}"
        if u.full:
            self.count.configure(text=f"{total} · hết lượt", text_color=T.DANGER)
            self.note.configure(text=f"{u.model} · Google báo hết lượt hôm nay — đặt lại lúc {u.reset}, hoặc đổi model "
                                     "ở Cài đặt › AI dùng API key", text_color=T.WARNING)
            return
        self.count.configure(text=f"{total} · {round(u.share * 100)}%",
                             text_color=T.TEXT if u.share < 0.8 else T.WARNING)
        self.note.configure(text=f"{u.model} · còn {max(0, u.limit - u.calls)} lượt · đặt lại lúc {u.reset}",
                            text_color=T.TEXT_MUTED)

    def _maybe_ask_balance(self, u: ai_usage.Usage, key: str) -> None:
        now = time.time()
        calls, at = self._asked
        stale = u.calls != calls or now - u.balance_ts > self.BALANCE_EVERY
        if self._asking or not stale or now - at < 60:
            return
        self._asking, self._asked = True, (u.calls, now)

        def work() -> None:
            try:
                fetch_balance(key)
            finally:
                self._asking = False            # chỉ đổi cờ — không đụng giao diện từ luồng nền

        threading.Thread(target=work, daemon=True).start()

    def _tick(self) -> None:
        self._job = None
        try:
            if self.winfo_ismapped():
                self.refresh()
        except Exception:                              # noqa: BLE001 — thanh đếm lỗi không được làm phiền người dùng
            pass
        self._job = self.after(self.TICK, self._tick)

    def destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:                          # noqa: BLE001
                pass
            self._job = None
        super().destroy()
