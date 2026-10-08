"""Vá nhỏ cho CustomTkinter — import một lần khi khởi động giao diện.

Vạch đen 1–2 px ở mép phải/dưới của nút (DPI 125 %/150 %): CTk đổi kích thước thật (px) về đơn vị logic bằng
``int(value / scaling)`` — làm tròn xuống (106 px → 84.8 → 84) — rồi khi vẽ lại nhân lên thành 105 px, hụt so với
canvas 106 px → lộ nền tối. Trả về số thực (84.8) thì vẽ đúng 106 px. Ngoài ra DrawEngine còn ép kích thước vẽ về số chẵn
(floor) nên mọi widget rộng/cao lẻ px (kể cả khi kéo giãn theo lưới) đều hở 1 px — tắt việc ép chẵn đó.
"""

from __future__ import annotations

import math
import tkinter

from customtkinter.windows.widgets.core_rendering import DrawEngine
from customtkinter.windows.widgets.ctk_button import CTkButton
from customtkinter.windows.widgets.ctk_frame import CTkFrame
from customtkinter.windows.widgets.ctk_label import CTkLabel
from customtkinter.windows.widgets.ctk_optionmenu import CTkOptionMenu
from customtkinter.windows.widgets.ctk_scrollable_frame import CTkScrollableFrame
from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
from customtkinter.windows.widgets.ctk_slider import CTkSlider
from customtkinter.windows.widgets.ctk_textbox import CTkTextbox
from customtkinter.windows.widgets.image import CTkImage
from customtkinter.windows.widgets.scaling import CTkScalingBaseClass


def _reverse_widget_scaling(self, value):
    if isinstance(value, (int, float)):
        return value / self._CTkScalingBaseClass__widget_scaling
    return value


_orig_draw = DrawEngine.draw_rounded_rect_with_border


def _draw_rounded_rect_with_border(self, width, height, corner_radius, border_width, overwrite_preferred_drawing_method=None):
    # Kiểu vẽ "font_shapes" chỉ vẽ đúng với kích thước chẵn. Widget KHÔNG viền: nâng LÊN chẵn (ceil) → vẽ dư 1 px bị
    # canvas cắt, không hở nền. Widget CÓ viền: giữ cách hạ xuống chẵn của CTk (nâng lên thì viền phải/dưới bị cắt mất),
    # 1 px hở là màu nền cha nên không thấy.
    width, height = round(width), round(height)          # 115.2 × 1.25 = 143.99999… → 144 trước khi xét chẵn/lẻ
    if round(border_width) <= 0:
        width = math.ceil(width / 2) * 2
        height = math.ceil(height / 2) * 2
    else:
        width = math.floor(width / 2) * 2
        height = math.floor(height / 2) * 2
    # Vẽ lại một nút/khung tốn ~30 lệnh Tcl (dời từng hình bo góc). CTk vẽ lại ở mọi lần configure, kể cả khi
    # kích thước y hệt lần trước → bỏ qua, chỉ kiểm tra hình còn trên canvas (1 lệnh). Màu vẫn do widget tự đặt sau.
    key = (width, height, round(corner_radius), round(border_width), overwrite_preferred_drawing_method)
    if self.__dict__.get("_es_rect") == key and self._canvas.find_withtag("inner_parts"):
        return False
    self.__dict__["_es_rect"] = key
    return _orig_draw(self, width, height, corner_radius, border_width, overwrite_preferred_drawing_method)


# Khung / nhãn «trong suốt» không viền (≈ 800 cái khi mở hết các tab): CTk vẫn vẽ một hình bo góc tô đúng màu nền
# — vô hình nhưng mỗi cái ~10 hình trên canvas và dời lại mỗi lần đổi cỡ. Chỉ đặt màu nền canvas là đủ. Widget nào
# từng có màu nền / viền (đã có hình) thì giữ cách vẽ gốc để hình cũ được tô lại đúng.
_orig_frame_draw = CTkFrame._draw
_orig_label_draw = CTkLabel._draw


def _plain(self) -> bool:
    return (self._fg_color == "transparent" and not round(getattr(self, "_border_width", 0) or 0)
            and not self.__dict__.get("_es_shape"))


def _frame_draw(self, no_color_updates=False):
    if _plain(self) and getattr(self, "_background_corner_colors", None) is None:
        if no_color_updates is False and self._canvas.winfo_exists():
            self._canvas.configure(bg=self._apply_appearance_mode(self._bg_color))
        return
    self.__dict__["_es_shape"] = True
    _orig_frame_draw(self, no_color_updates)


def _label_draw(self, no_color_updates=False):
    if _plain(self):
        if no_color_updates is False:
            bg = self._apply_appearance_mode(self._bg_color)
            self._canvas.configure(bg=bg)
            self._label.configure(fg=self._apply_appearance_mode(self._text_color),
                                  disabledforeground=self._apply_appearance_mode(self._text_color_disabled), bg=bg)
        return
    self.__dict__["_es_shape"] = True
    _orig_label_draw(self, no_color_updates)


# Mỗi CTkTextbox tự dò «có cần thanh cuộn» 5 lần/giây suốt đời, kể cả khi đang ẩn (tab khác) → ẩn thì bỏ qua và
# hỏi lại thưa (1 giây). Ô đang hiện vẫn dò như cũ.
_orig_tb_check = getattr(CTkTextbox, "_check_if_scrollbars_needed", None)


def _tb_check(self, event=None, continue_loop=False):
    if continue_loop and event is None:
        try:
            if not self._textbox.winfo_exists():
                return
            if not self.winfo_viewable():
                self.after(1000, lambda: self._check_if_scrollbars_needed(continue_loop=True))
                return
        except tkinter.TclError:
            return
    _orig_tb_check(self, event, continue_loop)


# Thanh cuộn: CTk vẽ lại mỗi lần set() dù vị trí không đổi (mỗi lần cuộn/đổi bố cục gọi vài lần) → bỏ lần trùng.
_orig_sb_set = CTkScrollbar.set


def _sb_set(self, start_value, end_value):
    key = (round(float(start_value), 4), round(float(end_value), 4), round(self._current_width), round(self._current_height))
    if self.__dict__.get("_es_set") == key:
        return
    self.__dict__["_es_set"] = key
    _orig_sb_set(self, start_value, end_value)


# CTkScrollbar / CTkOptionMenu: cuối _draw gọi self._canvas.update_idletasks() → ép Tk xử lý NGAY mọi việc đang chờ của
# cả cửa sổ (dàn trang hàng trăm widget), lồng nhau vì dàn trang lại làm thanh cuộn vẽ lại: mở tab lần đầu tốn 0,4–0,7 s
# chỉ ở đây, kéo giãn cửa sổ giật tới 0,4 s (đo: audit_out/perf). Canvas vẫn tự vẽ ở nhịp rảnh kế tiếp → bỏ lệnh ép đó.
def _no_flush(*_a, **_k):
    return None


def _make_no_flush_draw(orig):
    def _draw(self, *a, **k):
        cv = self.__dict__.get("_canvas")
        if cv is not None and "update_idletasks" not in cv.__dict__:
            cv.update_idletasks = _no_flush             # chỉ canvas riêng của widget này
        return orig(self, *a, **k)
    return _draw


# CTk gắn callback của CTkImage vào widget nhưng destroy() không gỡ → widget đã huỷ vẫn bị ảnh giữ lại.
# Gỡ khi huỷ để icons.icon() dùng chung được một CTkImage cho mọi nút (khỏi tạo lại ảnh mỗi lần vẽ).
def _make_destroy(cls):
    orig = cls.destroy

    def destroy(self):
        img = self.__dict__.get("_image")
        if isinstance(img, CTkImage):
            try:
                img.remove_configure_callback(self._update_image)
            except (ValueError, AttributeError):
                pass
        orig(self)

    return destroy


# CTkScrollableFrame đăng ký 5 handler toàn cục (bind_all: lăn chuột, Shift) mà destroy() không gỡ → mỗi popup /
# hộp thoại / lần đổi theme để lại handler «mồ côi»; mọi lần lăn chuột chạy hết chúng và giữ widget cũ trong bộ nhớ.
def _sf_bind_all(self, sequence=None, func=None, add=None):
    funcid = tkinter.Misc.bind_all(self, sequence, func, add)
    if funcid:
        self.__dict__.setdefault("_es_all_binds", []).append((sequence, funcid))
    return funcid


_orig_sf_destroy = CTkScrollableFrame.destroy


def _sf_destroy(self):
    for sequence, funcid in self.__dict__.pop("_es_all_binds", []):
        try:
            script = self.tk.call("bind", "all", sequence)
            keep = "\n".join(line for line in str(script).split("\n") if line.strip() and funcid not in line)
            self.tk.call("bind", "all", sequence, keep)
            self.tk.deletecommand(funcid)
            # Python ≥ 3.12 ghi lệnh bind_all vào danh sách dọn dẹp của cửa sổ gốc → gỡ ra, tránh xoá lần hai khi đóng app
            for owner in (self._root(), self):
                cmds = getattr(owner, "_tclCommands", None)
                if cmds and funcid in cmds:
                    cmds.remove(funcid)
        except Exception:
            pass
    _orig_sf_destroy(self)


# CustomTkinter 6.x: lăn chuột trên thanh trượt đổi giá trị (không chặn sự kiện) → cuộn bảng công cụ mà con trỏ lướt
# qua thanh nào là thanh đó bị đổi. Bỏ: thanh trượt chỉ đổi khi bấm / nắm kéo; lăn chuột vẫn cuộn bảng như thường.
def _slider_ignore_wheel(self, event=None):
    return None


# CTk 6.x cũng bỏ qua việc cuộn bảng khi con trỏ nằm trên thanh trượt (dành lăn chuột cho thanh trượt) → sau khi tắt
# lăn chuột của thanh trượt, coi thanh trượt như phần nền: lăn chuột trên nó vẫn cuộn bảng chứa nó.
_orig_valid_scroll = getattr(CTkScrollableFrame, "_check_if_valid_scroll", None)


def _valid_scroll_through_slider(self, widget):
    w = widget
    while w is not None and w != self._parent_canvas:
        if isinstance(w, CTkSlider):
            return _orig_valid_scroll(self, w.master)
        w = getattr(w, "master", None)
    return _orig_valid_scroll(self, widget)


def apply() -> None:
    if getattr(CTkScalingBaseClass, "_es_patched", False):
        return
    # CTk dò chế độ sáng/tối của hệ điều hành 33 lần/giây dù app luôn đặt sẵn «dark» (không theo hệ thống) → 1 lần/giây;
    # dò DPI màn hình 10 lần/giây cho mỗi cửa sổ → 2 lần/giây (kéo cửa sổ sang màn hình khác vẫn kịp đổi cỡ).
    try:
        from customtkinter.windows.widgets.appearance_mode import AppearanceModeTracker
        from customtkinter.windows.widgets.scaling import ScalingTracker

        AppearanceModeTracker.update_loop_interval = 1000
        ScalingTracker.update_loop_interval = 500
    except (ImportError, AttributeError):
        pass
    CTkScalingBaseClass._reverse_widget_scaling = _reverse_widget_scaling
    DrawEngine._round_width_to_even_numbers = False
    DrawEngine._round_height_to_even_numbers = False
    DrawEngine.draw_rounded_rect_with_border = _draw_rounded_rect_with_border
    CTkScrollbar.set = _sb_set
    CTkScrollbar._draw = _make_no_flush_draw(CTkScrollbar._draw)
    CTkOptionMenu._draw = _make_no_flush_draw(CTkOptionMenu._draw)
    CTkFrame._draw = _frame_draw
    if _orig_tb_check is not None:
        CTkTextbox._check_if_scrollbars_needed = _tb_check
    CTkLabel._draw = _label_draw
    CTkButton.destroy = _make_destroy(CTkButton)
    CTkLabel.destroy = _make_destroy(CTkLabel)
    CTkScrollableFrame.bind_all = _sf_bind_all
    CTkScrollableFrame.destroy = _sf_destroy
    if hasattr(CTkSlider, "_mouse_scroll_event"):         # chỉ có từ CustomTkinter 6.x
        CTkSlider._mouse_scroll_event = _slider_ignore_wheel
        if _orig_valid_scroll is not None:
            CTkScrollableFrame._check_if_valid_scroll = _valid_scroll_through_slider
    CTkScalingBaseClass._es_patched = True
