"""Khối «Âm thanh» (âm gốc / nền, khử giọng gốc, lồng tiếng to nhỏ, tự hạ âm gốc…) — nằm ở tab Lồng tiếng (trước ở tab
Video: âm thanh của lồng tiếng nằm hai nơi). Ghi vào cấu hình ``video`` như cũ (mẫu / cấu hình riêng không đổi)."""

from __future__ import annotations

from app.ui.widgets.common import Section, SliderRow, SwitchRow, hint, pack_rows


class AudioSection:
    """Dựng thẳng vào ``module`` (khối là con trực tiếp → «Sửa ›» ở tab Duyệt cuộn tới được)."""

    TITLE = "Âm thanh"

    def __init__(self, module) -> None:
        self.module = module
        self.state = module.state
        self.window = module.window
        sec = self.section = Section(module, self.TITLE, info="Lồng tiếng: bật «Khử giọng nói gốc», âm nền 60–90%, lồng "
                                                              "tiếng 120–160%. «Tự hạ âm gốc»: âm gốc giảm mượt khi có lời "
                                                              "đọc rồi lên lại.")
        sec.pack(fill="x", pady=4)
        self.keep = SwitchRow(sec.body, "Phát âm thanh gốc của video", on_change=self._set("keep_original_audio"))
        self.novocal = SwitchRow(sec.body, "Khử giọng nói gốc (AI) — giữ nhạc nền & tiếng động", on_change=self._novocal_toggled)
        self.novocal_hint = hint(sec.body, "")
        self.orig_vol = SliderRow(sec.body, "Âm gốc / nền", from_=0, to=200, step=5, suffix="%",
                                  on_change=self._set("original_volume", float))
        self.tts_vol = SliderRow(sec.body, "Lồng tiếng", from_=0, to=300, step=5, suffix="%",
                                 on_change=self._set("tts_volume", float))
        self.duck = SwitchRow(sec.body, "Tự hạ âm gốc khi có lời đọc", on_change=self._set("adaptive_ducking"))
        self.norm = SwitchRow(sec.body, "Chuẩn hoá độ to đầu ra", on_change=self._set("normalize_audio"))
        self.mute = SwitchRow(sec.body, "Video câm (tắt hết: âm gốc, lồng tiếng, nhạc)", on_change=self._set("mute"))
        self.loop = SwitchRow(sec.body, "Lặp video cho đủ thời lượng giọng đọc", on_change=self._set("loop_to_voice"))
        pack_rows(sec.body, [self.keep, self.novocal, self.novocal_hint, self.orig_vol, self.tts_vol, self.duck,
                             self.norm, self.mute, self.loop])

    def _set(self, field: str, cast=None):
        """Ghi một trường của cấu hình ``video`` (khối nằm ở tab Lồng tiếng nhưng là âm thanh của cả video)."""
        def cb(v) -> None:
            if self.module._loading:
                return
            cfg = self.state.video
            v = cast(v) if cast else v
            if getattr(cfg, field, None) == v:
                return
            setattr(cfg, field, v)
            self.state.config_changed("video")
        return cb

    def refresh(self) -> None:
        """Gọi trong lúc module đang nạp (``_loading``)."""
        cfg = self.state.video
        self.keep.set(cfg.keep_original_audio)
        self.orig_vol.set(cfg.original_volume)
        self.tts_vol.set(cfg.tts_volume)
        self.duck.set(cfg.adaptive_ducking)
        self.norm.set(cfg.normalize_audio)
        self.mute.set(cfg.mute)
        self.loop.set(cfg.loop_to_voice)
        self.novocal.set(cfg.demucs_enable)
        self.novocal_status()

    def _novocal_toggled(self, v: bool) -> None:
        if self.module._loading:
            return
        self._set("demucs_enable")(bool(v))
        if v and self.state.video.original_volume < 40:
            # trước đây âm gốc để nhỏ cho đỡ lẫn giọng; giờ chỉ còn nhạc & tiếng động → nâng lên mức nền dễ nghe
            self._set("original_volume", float)(70.0)
            self.orig_vol.set(70.0)
        self.novocal_status()
        self.window.preview.ensure_no_vocals()

    def novocal_status(self) -> None:
        from app.services.vocal_remove import cached_no_vocals, model_ready

        cfg = self.state.video
        if not cfg.demucs_enable:
            text = ("Tắt: giữ nguyên âm gốc (cả giọng nói). Bật khi lồng tiếng: AI bỏ giọng Trung / Anh… của video gốc, "
                    "giữ nhạc nền & tiếng động. Chạy trên máy, ~1 phút cho mỗi phút video, làm một lần ở bước Chuẩn bị.")
        elif not cfg.keep_original_audio or cfg.mute:
            text = "Đang tắt âm gốc (hoặc tắt toàn bộ âm thanh) nên không cần khử giọng — bước này sẽ được bỏ qua."
        else:
            it = self.state.active
            if it is not None and cached_no_vocals(it.path) is not None:
                text = "✓ Video này đã khử giọng gốc."
            elif not model_ready():
                text = "Lần đầu sẽ tải model AI (~64 MB). Chưa khử xong thì xem trước tạm vặn âm gốc rất nhỏ."
            else:
                text = "Video này sẽ được khử giọng khi Chuẩn bị / Xuất (xem trước tự khử nền nếu đang mở)."
        self.novocal_hint.configure(text=text)
