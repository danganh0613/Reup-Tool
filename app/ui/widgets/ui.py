"""Bộ thành phần chuẩn hoá theo tinh thần shadcn/ui (Button variants + sizes, Select trigger + content).

* ``button(master, text, icon=, variant=, size=)`` — variant: primary · secondary · outline · ghost · destructive;
  size: sm (32) · md (36) · lg (40) · icon (36×36). Chữ regular, icon cách chữ 6 px và hạ 1 px cho thẳng hàng,
  bề rộng tự đo theo chữ (đệm px-3/px-4).
* ``Select`` — ô chọn kiểu shadcn: viền 1 px, chữ căn trái, chevron mờ bên phải; bấm mở ``Popover`` có ✓ ở mục
  đang chọn, cuộn được, có ô lọc khi danh sách dài. ``chip=True`` cho kiểu chip trên khung xem trước.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable

import customtkinter as ctk

from app.config import theme as T
from app.ui.icons import icon

_KEY = "#010203"      # màu «trong suốt» của cửa sổ nổi (tooltip, popover) trên Windows — không dùng ở đâu khác
#: size → (cao, cỡ chữ, đệm ngang mỗi bên, cỡ icon) — theo shadcn/BoardUI: sm 32 · md 36 · lg 40; icon 14/16
SIZES = {"sm": (32, 12, 12, 14), "md": (36, 13, 14, 16), "lg": (40, 14, 18, 16), "icon": (36, 13, 0, 16)}
GAP = 6          # icon ↔ chữ (CTkButton._image_label_spacing, = gap 2 + label px 4 của BoardUI)
ICON_DY = 1      # icon hạ 1 px: phần trên của chữ (N, d, k) cao hơn phần dưới nên tâm nhìn của chữ thấp hơn tâm dòng
_LABEL_BORDER = 2   # tk.Label trong CTkButton có borderwidth=1 mỗi bên


def _variant(name: str) -> dict:
    if name == "primary":
        return {"fg_color": T.ACCENT, "hover_color": T.ACCENT_HOVER, "text_color": T.ON_ACCENT, "border_width": 0}
    if name == "outline":
        return {"fg_color": "transparent", "hover_color": T.BG_HOVER, "text_color": T.TEXT, "border_width": 1, "border_color": T.BORDER}
    if name == "ghost":
        return {"fg_color": "transparent", "hover_color": T.BG_HOVER, "text_color": T.TEXT, "border_width": 0}
    if name == "destructive":
        return {"fg_color": T.DANGER, "hover_color": T.DANGER_HOVER, "text_color": "#FFFFFF", "border_width": 0}
    return {"fg_color": T.BG_ELEVATED, "hover_color": T.BG_HOVER, "text_color": T.TEXT, "border_width": 0}


# ── Nút nền màu khi bị khoá (state="disabled") ───────────────────────────
# CTk giữ nguyên nền sáng nhưng đổi chữ sang xám → nút «Dừng» đỏ chữ xám, nút chính xanh chữ xám trông như lỗi.
# Ở đây: nút nền màu (chính / xoá-dừng) khi khoá → nền dịu đi (màu «soft» cùng tông), chữ + icon vẫn rõ, cùng một
# kiểu cho mọi nút trong app (kể cả nút tạo thẳng bằng CTkButton với T.primary_button() / T.danger_button()).
def _disabled_look(fg) -> tuple[str, str] | None:
    """(nền, chữ) khi khoá cho nút nền ``fg``; None = nút không nền màu (giữ kiểu mặc định của CTk)."""
    c = (fg[-1] if isinstance(fg, (list, tuple)) else fg or "")
    c = str(c).upper()
    if c == str(T.ACCENT).upper():
        return T.ACCENT_SOFT, T.ACCENT
    if c in (str(T.DANGER).upper(), str(T.DANGER_HOVER).upper()):
        return T.DANGER_SOFT, "#FFFFFF"
    return None


def _sync_state(b: ctk.CTkButton) -> None:
    on = getattr(b, "_es_on", None)
    if on is None:
        fg = b.cget("fg_color")
        look = _disabled_look(fg)
        on = b._es_on = (fg, look) if look else False
    disabled = str(b.cget("state")) == "disabled"
    kw: dict = {}                                # gom thay đổi → một lần configure (mỗi lần CTk vẽ lại cả nút)
    if on:
        fg, (soft_fg, soft_text) = on
        want = soft_fg if disabled else fg
        if b.cget("fg_color") != want:
            kw["fg_color"] = want
        if b.cget("text_color_disabled") != soft_text:
            kw["text_color_disabled"] = soft_text
    else:                                        # nút xám / trong suốt: icon mờ cùng chữ
        dis = b.cget("text_color_disabled")
        soft_text = dis[-1] if isinstance(dis, (list, tuple)) else dis
        if not str(soft_text).startswith("#"):          # tên màu Tk (vd. «gray60») → hex cho bộ vẽ icon
            try:
                r, g, bl = (v >> 8 for v in b.winfo_rgb(soft_text))
                soft_text = f"#{r:02X}{g:02X}{bl:02X}"
            except Exception:
                soft_text = T.TEXT_MUTED
    ic = getattr(b, "_es_icon", None)
    if ic and ic[0]:
        name, isz, color = ic[0], ic[1], ic[2]
        img = icon(name, isz, soft_text if disabled else color, dy=ICON_DY if str(b.cget("text")).strip() else 0)
        if img is not b.__dict__.get("_image"):        # icon() dùng chung ảnh → cùng tham số là cùng đối tượng
            kw["image"] = img
    if kw:
        _ORIG_CONFIGURE(b, **kw)


_ORIG_INIT = ctk.CTkButton.__init__
_ORIG_CONFIGURE = ctk.CTkButton.configure


def _btn_init(self, *a, **kw) -> None:
    look = _disabled_look(kw.get("fg_color"))
    if look and "text_color_disabled" not in kw:
        kw["text_color_disabled"] = look[1]
    _ORIG_INIT(self, *a, **kw)
    self._es_on = None
    _sync_state(self)


def _btn_configure(self, require_redraw: bool = False, **kw) -> None:
    if "fg_color" in kw:
        self._es_on = None                       # đổi màu nền → tính lại kiểu khi khoá theo màu mới
    _ORIG_CONFIGURE(self, require_redraw, **kw)
    if ("state" in kw or "fg_color" in kw) and hasattr(self, "_es_on"):
        _sync_state(self)


if not getattr(ctk.CTkButton, "_es_patched", False):
    ctk.CTkButton.__init__ = _btn_init
    ctk.CTkButton.configure = _btn_configure
    ctk.CTkButton._es_patched = True


def _scale(w) -> float:
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(w)) or 1.0
    except Exception:
        return 1.0


def text_width(text: str, font: ctk.CTkFont, master=None) -> int:
    """Bề rộng chữ theo đơn vị logic. ``CTkFont.measure`` đo bằng font gốc chưa nhân DPI (cỡ px logic) nên trả về
    thẳng, không chia scaling (chia thêm sẽ bị thiếu ~20 % ở DPI 125 % → chữ bị cắt)."""
    try:
        return int(font.measure(text))
    except Exception:
        return 8 * len(text)


def button_width(master, text: str, *, size: str = "md", icon: bool = False, font: ctk.CTkFont | None = None) -> int:
    """Bề rộng nút vừa khít chữ (+ icon) theo ``size``; nút không chữ = hình vuông ``cao×cao``."""
    h, fs, px, isz = SIZES.get(size, SIZES["md"])
    text = text.strip()
    if size == "icon" or not text:
        return h
    w = text_width(text, font or T.font(fs), master) + _LABEL_BORDER + 2 * px
    if icon:
        w += isz + _LABEL_BORDER + GAP
    return w


def button(master, text: str = "", *, icon_name: str | None = None, variant: str = "secondary", size: str = "md",
           command: Callable[[], None] | None = None, width: int | None = None, icon_color: str | None = None,
           min_width: int = 0, tip: str | None = None, **kw) -> ctk.CTkButton:
    """Nút chuẩn: chữ regular (primary: semibold), icon Fluent đứng trước chữ cách 6 px và hạ 1 px cho thẳng hàng với chữ."""
    h, fs, px, isz = SIZES.get(size, SIZES["md"])
    style = _variant(variant)
    style.update({k: kw.pop(k) for k in list(kw) if k in style or k == "border_color"})   # cho phép ghi đè màu lẻ
    font = kw.pop("font", None) or T.font(fs, "medium" if variant == "primary" else "normal")   # nút chính đậm hơn một chút
    img = icon(icon_name, isz, icon_color or style["text_color"], dy=ICON_DY) if icon_name else None
    text = text.strip()
    if width is None:
        width = max(button_width(master, text, size=size, icon=img is not None, font=font), min_width)
    b = ctk.CTkButton(master, text=text, image=img, compound="left", height=h, width=width, corner_radius=T.RADIUS,
                      font=font, command=command, **style, **kw)
    b._es_icon = (icon_name, isz, icon_color or style["text_color"], size, font)   # để relabel() vẽ lại icon/đo chữ đúng
    if str(b.cget("state")) == "disabled":
        _sync_state(b)                           # icon theo màu chữ khi khoá
    if tip:
        b.tooltip = Tooltip(b, tip)
    return b


def relabel(b: ctk.CTkButton, text: str, *, width: int | None = None) -> None:
    """Đổi chữ nút tạo bằng ``button()``: ``""`` → chỉ icon (icon về giữa, nút vuông); có chữ → icon hạ 1 px, rộng tự đo."""
    name, isz, color, size, font = getattr(b, "_es_icon", (None, 16, T.TEXT, "md", None))
    text = text.strip()
    kw = {"text": text}
    if name:
        kw["image"] = icon(name, isz, color, dy=ICON_DY if text else 0)
    kw["width"] = width if width is not None else button_width(b.master, text, size=size, icon=bool(name), font=font)
    b.configure(**kw)
    if str(b.cget("state")) == "disabled":
        _sync_state(b)


def button_icon(name: str, *, size: str = "md", color: str | None = None, with_text: bool = True) -> ctk.CTkImage | None:
    """Icon thay thế cho nút đã tạo bằng ``button()`` (cùng cỡ, cùng độ hạ) — dùng khi đổi trạng thái Phát/Dừng, Sub/Sub ẩn.
    ``with_text=False`` cho nút chỉ có icon (icon nằm đúng giữa)."""
    isz = SIZES.get(size, SIZES["md"])[3]
    return icon(name, isz, color or T.TEXT, dy=ICON_DY if with_text else 0)


# ── Tooltip ───────────────────────────────────────────────────────────────

class Tooltip:
    """Chú thích nhỏ hiện dưới widget khi rê chuột ~0,45 s (kiểu shadcn: nền sáng, chữ tối, bo góc).

    Gắn cho widget và mọi widget con (CTk tạo canvas/label con) — rời sang widget con không làm tắt tooltip.
    ``Tooltip(widget, "Hoàn tác (Ctrl+Z)")`` hoặc ``button(..., tip="…")``.
    """

    DELAY = 450
    _open: Tooltip | None = None          # chỉ một tooltip hiện tại một thời điểm

    def __init__(self, widget, text: str, *, delay: int | None = None) -> None:
        self.w, self.text = widget, text
        self.delay = self.DELAY if delay is None else delay
        self._after: str | None = None
        self._hide_after: str | None = None
        self._tip: tk.Toplevel | None = None
        self._bind_tree(widget)

    def _bind_tree(self, w) -> None:
        tk.Misc.bind(w, "<Enter>", self._enter, add="+")
        tk.Misc.bind(w, "<Leave>", self._leave, add="+")
        tk.Misc.bind(w, "<ButtonPress>", self._press, add="+")
        for ch in w.winfo_children():
            self._bind_tree(ch)

    def set_text(self, text: str) -> None:
        self.text = text

    # ── sự kiện ──
    def _enter(self, _e=None) -> None:
        self._cancel_hide()
        if self._tip is not None:
            return
        self._cancel()
        self._after = self.w.after(self.delay, self._show)

    def _leave(self, _e=None) -> None:
        self._cancel()
        self._cancel_hide()
        self._hide_after = self.w.after(60, self.hide)     # 60 ms: đủ để <Enter> của widget con huỷ lệnh ẩn

    def _press(self, _e=None) -> None:
        self._cancel()
        self.hide()

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.w.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _cancel_hide(self) -> None:
        if self._hide_after is not None:
            try:
                self.w.after_cancel(self._hide_after)
            except tk.TclError:
                pass
            self._hide_after = None

    # ── hiện / ẩn ──
    def _show(self) -> None:
        self._after = None
        if not self.text or not self.w.winfo_exists() or self._tip is not None:
            return
        if Tooltip._open is not None and Tooltip._open is not self:
            Tooltip._open.hide()
        k = _scale(self.w)
        tip = tk.Toplevel(self.w)
        tip.withdraw()
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
            tip.attributes("-transparentcolor", _KEY)           # nền trong suốt → bo góc thật (Windows)
            key = _KEY
        except tk.TclError:
            key = T.BG
        font = tkfont.Font(family=T.UI_FONT, size=-round(12 * k))
        pad_x, pad_y, r = round(10 * k), round(6 * k), round(6 * k)
        lines = self.text.split("\n")
        tw = max(font.measure(ln) for ln in lines) + 2 * pad_x
        th = font.metrics("linespace") * len(lines) + 2 * pad_y
        cv = tk.Canvas(tip, width=tw, height=th, bg=key, highlightthickness=0, bd=0)
        cv.pack()
        bg, fg = T.TEXT, T.BG                                  # đảo màu như shadcn (bg-primary / text-primary-foreground)
        cv.create_rectangle(r, 0, tw - r, th, fill=bg, outline="")
        cv.create_rectangle(0, r, tw, th - r, fill=bg, outline="")
        for ox, oy in ((0, 0), (tw - 2 * r, 0), (0, th - 2 * r), (tw - 2 * r, th - 2 * r)):
            cv.create_oval(ox, oy, ox + 2 * r, oy + 2 * r, fill=bg, outline="")
        cv.create_text(tw / 2, th / 2, text=self.text, fill=fg, font=font, justify="center")
        # đặt dưới widget, căn giữa; hết chỗ thì đặt phía trên; kẹp trong màn hình
        x = self.w.winfo_rootx() + (self.w.winfo_width() - tw) // 2
        y = self.w.winfo_rooty() + self.w.winfo_height() + round(6 * k)
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        if y + th > sh - 8:
            y = self.w.winfo_rooty() - th - round(6 * k)
        x = max(4, min(x, sw - tw - 4))
        tip.geometry(f"{tw}x{th}+{x}+{y}")
        tip.deiconify()
        self._tip = tip
        Tooltip._open = self

    def hide(self) -> None:
        self._hide_after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
        if Tooltip._open is self:
            Tooltip._open = None


def tooltip(widget, text: str) -> Tooltip:
    return Tooltip(widget, text)


# ── Select ────────────────────────────────────────────────────────────────

class Popover(tk.Toplevel):
    """Danh sách chọn nổi (SelectContent): cuộn khi dài, ô lọc khi > 12 mục, ✓ ở mục đang chọn, Esc / bấm ngoài để đóng."""

    ROW_H = 32
    MAX_ROWS = 9

    def __init__(self, anchor: tk.Widget, values: list[str], current: str, on_pick: Callable[[str], None],
                 *, labels: dict[str, str] | None = None, width: int = 220) -> None:
        super().__init__(anchor)
        self.withdraw()                        # dựng xong, đặt đúng chỗ rồi mới hiện → không nháy ở góc (0,0)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        # góc bo: phần ngoài góc của cửa sổ trong suốt (Windows) → không còn ô xám ở 4 góc; viền vẽ ngay trong khung
        key = T.BG
        try:
            self.attributes("-transparentcolor", _KEY)
            key = _KEY
        except tk.TclError:
            pass
        self.configure(bg=key)
        self._on_pick = on_pick
        self._values, self._current, self._labels = list(values), current, labels or {}
        k = _scale(anchor)
        self._k = k
        self._row_font = T.font(13)
        # rộng ít nhất bằng ô chọn; nhãn dài thì nới tới tối đa 520 px để đọc đủ (dài hơn nữa: tooltip khi rê chuột)
        longest = max((text_width(self._labels.get(v, v), self._row_font) for v in self._values), default=0)
        self._pw = max(width, int(anchor.winfo_width() / k), min(longest + 64, 520))   # (không dùng _w: tên nội bộ tkinter)
        box = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER, bg_color=key)
        box.pack()
        self._box = box
        self._filter_var = tk.StringVar()
        self._fill_after: str | None = None
        self._chunk_after: str | None = None
        self._pending: list[str] = []
        if len(self._values) > 12:
            e = ctk.CTkEntry(box, textvariable=self._filter_var, placeholder_text="Gõ để lọc…", height=30, width=self._pw - 12,
                             corner_radius=8, font=T.font(12))
            e.pack(padx=6, pady=(6, 2))
            self._filter_var.trace_add("write", lambda *_a: self._schedule_fill())
            self._entry = e
        else:
            self._entry = None
        rows = min(self.MAX_ROWS, len(self._values))
        if len(self._values) > self.MAX_ROWS:      # dài → khung cuộn; ngắn → khung thường (không có thanh cuộn thừa)
            self.list = ctk.CTkScrollableFrame(box, fg_color="transparent", width=self._pw - 12, height=rows * (self.ROW_H + 2) + 6)
        else:
            self.list = ctk.CTkFrame(box, fg_color="transparent", width=self._pw - 12)
        self.list.pack(padx=5, pady=5)
        self._fill()
        self.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, min(x, sw - w - 8))
        if y + h > sh - 8:
            y = max(0, anchor.winfo_rooty() - h - 4)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.bind("<Escape>", lambda _e=None: self.close())
        self.bind("<ButtonPress-1>", self._click)
        self.after(20, self._grab)

    CHUNK = 30          # số dòng dựng mỗi nhịp: danh sách dài (font hệ thống, ~400 giọng Edge) hiện ngay, phần sau dựng dần

    def _schedule_fill(self) -> None:
        """Gõ ô lọc: đợi ngừng gõ ~150 ms rồi mới dựng lại danh sách."""
        if self._fill_after is not None:
            self.after_cancel(self._fill_after)
        self._fill_after = self.after(150, self._fill)

    def _cancel_pending(self) -> None:
        for attr in ("_fill_after", "_chunk_after"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def _fill(self) -> None:
        self._cancel_pending()
        for w in self.list.winfo_children():
            w.destroy()
        q = self._filter_var.get().strip().lower()
        shown = [v for v in self._values if not q or q in self._labels.get(v, v).lower()]
        if not shown:
            ctk.CTkLabel(self.list, text="Không có mục nào", font=T.font(12), text_color=T.TEXT_MUTED).pack(pady=8)
            return
        self._pending = shown[:400]
        self._render_chunk()

    def _render_chunk(self) -> None:
        self._chunk_after = None
        if not self.winfo_exists():
            return
        batch, self._pending = self._pending[:self.CHUNK], self._pending[self.CHUNK:]
        check = icon("check", 14, T.ACCENT, dy=ICON_DY)
        row_w = self._pw - 16
        avail = row_w - 24 - 22                              # trừ đệm hai bên + chỗ dấu ✓
        for v in batch:
            cur = v == self._current
            label = self._labels.get(v, v)
            b = ctk.CTkButton(self.list, text=label, anchor="w", height=self.ROW_H, width=row_w, corner_radius=6,
                              fg_color=T.BG_ELEVATED if cur else "transparent", hover_color=T.BG_HOVER, text_color=T.TEXT,
                              font=self._row_font, image=check if cur else None, compound="right",
                              command=lambda val=v: self._pick(val))
            b.pack(fill="x", padx=2, pady=1)
            if text_width(label, self._row_font) > avail:         # nhãn dài hơn khung → rê chuột thấy đủ
                Tooltip(b, label, delay=300)
        if self._pending:
            self._chunk_after = self.after(1, self._render_chunk)

    def _grab(self) -> None:
        try:
            self.grab_set()
            (self._entry or self).focus_force()
        except tk.TclError:
            pass

    def _click(self, e) -> None:
        x0, y0 = self.winfo_rootx(), self.winfo_rooty()
        if not (x0 <= e.x_root <= x0 + self.winfo_width() and y0 <= e.y_root <= y0 + self.winfo_height()):
            anchor = self.master
            self.close()
            # bấm ra ngoài trúng một ô chọn KHÁC → mở luôn ô đó (khỏi phải bấm lần hai)
            try:
                w = anchor.winfo_containing(e.x_root, e.y_root)
                while w is not None and not isinstance(w, Select):
                    w = w.master
                if w is not None and w is not anchor:
                    w.after(10, w.open)
            except Exception:
                pass

    def _pick(self, v: str) -> None:
        self.close()
        self._on_pick(v)

    def close(self) -> None:
        self._cancel_pending()
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass


class Select(ctk.CTkFrame):
    """SelectTrigger: [icon] giá trị … chevron. ``chip=True``: nền nổi, không viền (chip trên khung xem trước)."""

    def __init__(self, master, values: list[str], *, value: str | None = None, command: Callable[[str], None] | None = None,
                 variable: tk.StringVar | None = None, width: int = 200, height: int = 36, labels: dict[str, str] | None = None,
                 short: Callable[[str], str] | None = None, icon_name: str | None = None, chip: bool = False,
                 placeholder: str = "Chọn…", menu_width: int | None = None, font: ctk.CTkFont | None = None,
                 tip: str | None = None) -> None:
        self._chip = chip
        self._bg = T.BG_ELEVATED if chip else T.BG_INPUT
        super().__init__(master, fg_color=self._bg, corner_radius=T.RADIUS, width=width, height=height,
                         border_width=0 if chip else 1, border_color=T.BORDER)
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)          # hàng cao bằng ô → icon/chữ/chevron căn giữa theo chiều dọc
        lh = max(20, height - 8)                     # nhãn thấp hơn ô để không đè lên viền bo góc
        self._values = list(values) if values else []
        self._labels = labels or {}
        self._short = short or (lambda v: self._labels.get(v, v))
        self._command = command
        self.var = variable or tk.StringVar(value=value if value is not None else (self._values[0] if self._values else ""))
        if variable is not None and value is not None:
            self.var.set(value)
        self._placeholder = placeholder
        self._menu_width = menu_width or max(200, width)
        self._state = "normal"
        self._fs = font or T.font(12 if chip else 13)
        self._has_icon = bool(icon_name)
        if icon_name:
            ctk.CTkLabel(self, text="", image=icon(icon_name, 15, T.TEXT_DIM, dy=ICON_DY), height=lh).grid(
                row=0, column=0, padx=(10, 0), sticky="w")
        self.label = ctk.CTkLabel(self, text="", anchor="w", font=self._fs, text_color=T.TEXT, height=lh)
        self.label.grid(row=0, column=1, sticky="ew", padx=(8 if icon_name else 12, 4))
        self.chev = ctk.CTkLabel(self, text="", image=icon("chevron", 11, T.TEXT_MUTED, dy=ICON_DY), height=lh)
        self.chev.grid(row=0, column=2, padx=(0, 10), sticky="e")
        # winfo_children() đã gồm label/chev — không liệt kê lại (bind 2 lần → bấm vào chữ mở rồi đóng ngay: "lúc được lúc không")
        for w in (self, *self.winfo_children()):
            w.bind("<Button-1>", lambda _e=None: self.open())
            w.bind("<Enter>", lambda _e=None: self._hover(True))
            w.bind("<Leave>", lambda _e=None: self._hover(False))
        self._opened_at = 0.0
        self.configure(cursor="hand2")
        tk.Misc.bind(self, "<Configure>", lambda _e: self._update_value_tip(self.label.cget("text")), add="+")
        self._menu: Popover | None = None
        self._tip_text = tip or ""
        self.tooltip = Tooltip(self, tip or "")
        self.var.trace_add("write", lambda *_a: self._render())
        self._render()

    # ── API tương thích CTkOptionMenu / CTkComboBox ──
    def get(self) -> str:
        return self.var.get()

    def set(self, v: str) -> None:
        if v != self.var.get():                 # gán trùng vẫn kích trace → vẽ lại nhãn vô ích
            self.var.set(v)

    def set_values(self, values: list[str], keep: bool = True) -> None:
        self._values = list(values) if values else []
        if not keep or self.var.get() not in self._values:
            self.var.set(self._values[0] if self._values else "")

    def cget(self, key: str):
        if key == "values":
            return list(self._values)
        if key == "state":
            return self._state
        return super().cget(key)

    def configure(self, require_redraw=False, **kw):
        if "values" in kw:
            self.set_values(kw.pop("values"), keep=True)
        if "state" in kw:
            self._state = kw.pop("state")
            self.label.configure(text_color=T.TEXT_MUTED if self._state == "disabled" else T.TEXT)
            self._shown = None
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().configure(require_redraw, **kw)

    # ── Vẽ / tương tác ──
    def _render(self) -> None:
        v = self.var.get()
        text = self._short(v) if v else self._placeholder
        color = T.TEXT if (v and self._state != "disabled") else T.TEXT_MUTED
        if self.__dict__.get("_shown") == (text, color):      # không đổi → khỏi vẽ lại (CTkLabel vẽ lại cả khung)
            return
        self._shown = (text, color)
        self.label.configure(text=text, text_color=color)
        if self._chip:      # chip vừa khít: đệm 10 · [icon 15 · 8] · chữ · 4 · chevron 11 · 10 (+6 dự phòng làm tròn DPI)
            w = text_width(text, self._fs, self) + (10 + 15 + 8 if self._has_icon else 12) + 4 + 11 + 10 + 6
            super().configure(width=max(56, min(260, w)))
        self._update_value_tip(text)

    def _update_value_tip(self, text: str) -> None:
        """Chữ dài hơn ô (bị cắt) → tooltip ghi đủ giá trị, kèm mô tả tính năng nếu có."""
        try:
            avail = self.winfo_width() / _scale(self) - (10 + 15 + 8 if self._has_icon else 12) - 4 - 11 - 10
        except tk.TclError:
            return
        cut = avail > 20 and text_width(text, self._fs, self) > avail
        full = text if cut else ""
        self.tooltip.set_text("\n".join(p for p in (full, self._tip_text) if p))

    def _hover(self, on: bool) -> None:
        if self._state == "disabled" or not self.winfo_exists():
            return
        try:
            super().configure(fg_color=T.BG_HOVER if on else self._bg)
        except tk.TclError:      # ô đang bị huỷ (đóng hộp thoại khi chuột còn ở trên)
            pass

    def open(self) -> None:
        if self._state == "disabled":
            return
        import time

        if time.monotonic() - self._opened_at < 0.12:       # 2 sự kiện của cùng một cú bấm → bỏ qua cái sau
            return
        self._opened_at = time.monotonic()
        if self._menu is not None and self._menu.winfo_exists():
            self._menu.close()
            self._menu = None
            return
        if self.tooltip is not None:
            self.tooltip.hide()
        self._menu = Popover(self, self._values, self.var.get(), self._picked, labels=self._labels, width=self._menu_width)

    def _picked(self, v: str) -> None:
        self.var.set(v)
        if self._command:
            self._command(v)
