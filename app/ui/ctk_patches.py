"""Vá nhỏ cho CustomTkinter — import một lần khi khởi động giao diện.

Vạch đen 1–2 px ở mép phải/dưới của nút (DPI 125 %/150 %): CTk đổi kích thước thật (px) về đơn vị logic bằng
``int(value / scaling)`` — làm tròn xuống (106 px → 84.8 → 84) — rồi khi vẽ lại nhân lên thành 105 px, hụt so với
canvas 106 px → lộ nền tối. Trả về số thực (84.8) thì vẽ đúng 106 px. Ngoài ra DrawEngine còn ép kích thước vẽ về số chẵn
(floor) nên mọi widget rộng/cao lẻ px (kể cả khi kéo giãn theo lưới) đều hở 1 px — tắt việc ép chẵn đó.
"""

from __future__ import annotations

import math

from customtkinter.windows.widgets.core_rendering import DrawEngine
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
    return _orig_draw(self, width, height, corner_radius, border_width, overwrite_preferred_drawing_method)


def apply() -> None:
    if getattr(CTkScalingBaseClass, "_es_patched", False):
        return
    CTkScalingBaseClass._reverse_widget_scaling = _reverse_widget_scaling
    DrawEngine._round_width_to_even_numbers = False
    DrawEngine._round_height_to_even_numbers = False
    DrawEngine.draw_rounded_rect_with_border = _draw_rounded_rect_with_border
    CTkScalingBaseClass._es_patched = True
