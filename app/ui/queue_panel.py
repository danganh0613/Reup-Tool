"""Hàng đợi video: thêm/xoá, chọn video đang làm việc, theo dõi tiến độ."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import SUBS_DIR
from app.ui.widgets.ui import Tooltip, button
from app.core.state import VideoItem
from app.services.srt_utils import format_ts
from app.core.platform import open_path

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".mpg", ".mpeg"}
_UNSET = object()
_STATUS_COLOR = {"Chờ": T.TEXT_MUTED, "Đang xử lý": T.INFO, "Xong": T.SUCCESS, "Lỗi": T.DANGER, "Đã huỷ": T.WARNING,
                 "Đang chuẩn bị": T.INFO, "Chờ duyệt": T.WARNING, "Đã duyệt": T.ACCENT, "Bỏ qua": T.TEXT_MUTED,
                 "Đã bỏ qua": T.WARNING, "Cần xuất lại": T.WARNING}
_RUNNING = ("Chờ", "Đang xử lý", "Đang chuẩn bị")          # còn trong lượt đang chạy


def has_work(it: VideoItem) -> bool:
    """Video đã có phần việc (phụ đề, bản dịch, giọng, cấu hình riêng, đã duyệt) — xoá khỏi hàng đợi thì nên hỏi."""
    return bool(it.srt_path or it.srt_translated_path or it.voice_path or it.config is not None or it.review)


class QueueRow(ctk.CTkFrame):
    def __init__(self, master, panel: QueuePanel, index: int, item: VideoItem) -> None:
        super().__init__(master, fg_color=T.BG_CARD, corner_radius=8, border_width=1, border_color=T.BORDER_SOFT)
        self.panel, self.index, self.item = panel, index, item
        self._shown: dict[str, object] = {}       # giá trị đã vẽ → configure (vẽ lại widget CTk) chỉ khi đổi
        self.grid_columnconfigure(1, weight=1)
        self.num = ctk.CTkLabel(self, text=f"{index + 1}", width=26, font=T.font(12, "bold"), text_color=T.TEXT_MUTED)
        self.num.grid(row=0, column=0, rowspan=2, padx=(8, 4), pady=6)
        self.name = ctk.CTkLabel(self, text=item.name, font=T.font(12, "bold"), anchor="w")
        self.name.grid(row=0, column=1, sticky="ew", pady=(6, 0))
        self.info = ctk.CTkLabel(self, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        self.info.grid(row=1, column=1, sticky="ew", pady=(0, 6))
        self.status = ctk.CTkLabel(self, text=item.status, font=T.font(11, "bold"), width=110, anchor="e")
        self.status.grid(row=0, column=2, padx=8, pady=(6, 0), sticky="e")
        self.bar = ctk.CTkProgressBar(self, width=110, height=8)
        self.bar.grid(row=1, column=2, padx=8, pady=(0, 6), sticky="e")
        self.bar.set(0)
        # video lỗi / đã huỷ: thử lại ngay tại đây (chuẩn bị hoặc render tuỳ chế độ), rê chuột xem lỗi đầy đủ
        self.retry_btn = button(self, "", icon_name="refresh", variant="ghost", size="sm", command=self._retry,
                                icon_color=T.ACCENT, width=28, tip="Thử lại video này")
        self.remove_btn = button(self, "", icon_name="close", variant="ghost", size="sm", command=self._remove, icon_color=T.TEXT_DIM, width=28,
                                 tip="Bỏ video này khỏi hàng đợi")
        self.remove_btn.grid(row=0, column=4, rowspan=2, padx=(0, 8))
        self.err_tip = Tooltip(self.info, "")
        for w in (self, self.num, self.name, self.info, self.status):
            w.bind("<Button-1>", lambda _e: self.panel.state.set_active(self.index))
            w.bind("<Double-Button-1>", lambda _e: self._open_output())
        self._shown.update(num=f"{index + 1}", name=item.name)
        self.update_view()

    def _remove(self) -> None:
        """✕: đang chạy lượt có video này → bỏ qua video (các video sau chạy tiếp); không thì bỏ khỏi hàng đợi."""
        win, it = self.panel.window, self.item
        if win.runner.running and it.status in _RUNNING:
            if messagebox.askyesno("Edit Studio", f"Bỏ qua «{it.name}» trong lượt đang chạy?\n\nCác video khác vẫn chạy tiếp."):
                win.skip_item(it)
            return
        if has_work(it) and not messagebox.askyesno(
                "Edit Studio", f"Bỏ «{it.name}» khỏi hàng đợi?\n\nPhụ đề, bản dịch, giọng đã làm vẫn còn trong thư mục "
                               "output — nạp lại video này sẽ được hỏi dùng lại."):
            return
        self.panel.state.remove_video(self.index)

    def _retry(self) -> None:
        self.panel.window.retry_item(self.item)

    def _open_output(self) -> None:
        p = self.item.output_path or self.item.path
        if p and Path(p).exists():
            open_path(p)

    def _put(self, key: str, value: object, apply) -> None:
        if self._shown.get(key, _UNSET) != value:
            self._shown[key] = value
            apply(value)

    def assign(self, index: int, item: VideoItem) -> None:
        """Dùng lại dòng cho video khác / vị trí khác (khỏi hủy và dựng lại cả dòng)."""
        self.index, self.item = index, item
        self._put("num", f"{index + 1}", lambda v: self.num.configure(text=v))
        self._put("name", item.name, lambda v: self.name.configure(text=v))
        self.update_view()

    def update_view(self) -> None:
        it = self.item
        parts = []
        if it.width:
            parts.append(f"{it.width}×{it.height}")
        if it.duration:
            parts.append(format_ts(it.duration, ".")[:8])
        flags = []
        if it.srt_path:
            flags.append("SRT")
        if it.srt_translated_path:
            flags.append("Dịch")
        if it.voice_path:
            flags.append("Giọng")
        if it.config is not None:
            flags.append("✎ Riêng")
        if flags:
            parts.append(" · ".join(flags))
        if it.message and it.status in ("Đang xử lý", "Lỗi"):
            parts.append(it.message)
        self._put("info", "  |  ".join(parts) if parts else "Chưa đọc thông tin", lambda v: self.info.configure(text=v))
        self._put("status", it.status, lambda v: self.status.configure(text=v, text_color=_STATUS_COLOR.get(v, T.TEXT)))
        # ↻: video lỗi / huỷ / bỏ qua → thử lại; đã xong → xuất lại riêng video này
        again = it.status in ("Lỗi", "Đã huỷ", "Đã bỏ qua", "Xong", "Cần xuất lại")
        self._put("retry", again, lambda v: self.retry_btn.grid(row=0, column=3, rowspan=2) if v else self.retry_btn.grid_remove())
        self._put("retry_tip", "Xuất lại video này" if it.status in ("Xong", "Cần xuất lại") else "Thử lại video này",
                  lambda v: self.retry_btn.tooltip.set_text(v))
        running = self.panel.window.runner.running and it.status in _RUNNING
        self._put("remove_tip", "Bỏ qua video này (các video khác chạy tiếp)" if running else "Bỏ video này khỏi hàng đợi",
                  lambda v: self.remove_btn.tooltip.set_text(v))
        if it.status == "Lỗi" and it.message:
            import textwrap

            self.err_tip.set_text("\n".join(textwrap.wrap(f"Lỗi: {it.message}", 70)[:8]) + "\n\nBấm ↻ để thử lại.")
        else:
            self.err_tip.set_text("")
        self._put("progress", round(max(0.0, min(1.0, it.progress)), 3), self.bar.set)
        active = self.panel.state.active_index == self.index
        self._put("active", active, lambda v: self.configure(border_color=T.ACCENT if v else T.BORDER_SOFT,
                                                              border_width=2 if v else 1))


class QueuePanel(ctk.CTkFrame):
    def __init__(self, master, window: MainWindow, *, compact: bool = False, height: int = 220) -> None:
        super().__init__(master, fg_color="transparent")
        self.window = window
        self.state = window.app_state
        self.rows: list[QueueRow] = []
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.count_label = ctk.CTkLabel(head, text="HÀNG ĐỢI", font=T.font(11, "bold"), anchor="w", text_color=T.TEXT_DIM)
        self.count_label.pack(side="left")
        self.count_right = ctk.CTkLabel(head, text="0 video", font=T.font(11), text_color=T.TEXT_MUTED)
        self.count_right.pack(side="right")
        if not compact:
            button(head, "Xoá hết", icon_name="trash", variant="destructive", size="sm", command=self._clear).pack(
                side="right", padx=(4, 0))
            button(head, "Thư mục", icon_name="add", size="sm", command=self._add_folder).pack(side="right", padx=(4, 0))
            button(head, "Thêm video", icon_name="add", variant="primary", size="sm", command=self._add_files).pack(side="right")

        self.list = ctk.CTkScrollableFrame(self, fg_color=T.BG_INPUT, corner_radius=8, height=height)
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        self.empty = ctk.CTkLabel(self.list, text="Chưa có video — bấm Nạp video hoặc kéo thả vào cửa sổ.",
                                  text_color=T.TEXT_MUTED, font=T.font(12))

        self.state.on("videos_changed", self.rebuild)
        self.state.on("active_changed", self._highlight)
        self.rebuild()

    def destroy(self) -> None:
        # panel bị dựng lại khi đổi theme → gỡ listener cũ, không thì mỗi sự kiện gọi vào widget đã hủy (TclError + log)
        self.state.off("videos_changed", self.rebuild)
        self.state.off("active_changed", self._highlight)
        super().destroy()

    # ── Thêm / xoá ───────────────────────────────────────────────────────
    def add_files(self) -> None:
        self._add_files()

    def add_folder(self) -> None:
        self._add_folder()

    def clear(self) -> None:
        self._clear()

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Chọn video", filetypes=[("Video", " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))), ("Tất cả", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(title="Chọn thư mục chứa video")
        if d:
            self.add_paths([str(p) for p in sorted(Path(d).iterdir()) if p.suffix.lower() in VIDEO_EXTS])

    def add_paths(self, paths: list[str]) -> None:
        vids: list[str] = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                vids.extend(str(x) for x in sorted(pp.iterdir()) if x.suffix.lower() in VIDEO_EXTS)
            elif pp.suffix.lower() in VIDEO_EXTS:
                vids.append(str(pp))
        added = self.state.add_videos(vids)
        if added:
            # tự tìm SRT cùng tên cạnh video
            for it in added:
                srt = Path(it.path).with_suffix(".srt")
                if srt.exists():
                    it.srt_path = str(srt)
            self._offer_reuse([it for it in added if not it.srt_path])
            self.state.set_active(len(self.state.videos) - len(added))
            self.state.emit("active_changed")
            self.window.log(f"Đã thêm {len(added)} video.")

    def _offer_reuse(self, items: list[VideoItem]) -> None:
        """Video từng làm (phụ đề / bản dịch còn trong output/subs) → hỏi dùng lại, khỏi nhận diện + dịch lại."""
        pipe = self.window.pipeline
        found = []
        for it in items:
            srt, tr = SUBS_DIR / f"{pipe.file_stem(it)}.srt", pipe.translated_path(it)
            if srt.exists():
                found.append((it, srt, tr if tr.exists() else None))
        if not found:
            return
        names = ", ".join(it.name for it, _, _ in found[:3]) + (f" và {len(found) - 3} video khác" if len(found) > 3 else "")
        if not messagebox.askyesno("Edit Studio", f"Đã có phụ đề làm trước cho {names}.\n\nDùng lại? "
                                                  "(Không = nhận diện lại từ đầu)"):
            return
        for it, srt, tr in found:
            it.srt_path = str(srt)
            it.srt_translated_path = str(tr) if tr else None
        self.window.log(f"Dùng lại phụ đề đã làm cho {len(found)} video.")

    def _clear(self) -> None:
        if self.window.runner.running:
            messagebox.showinfo("Edit Studio", "Đang chạy — bấm Dừng trước khi xoá hàng đợi.")
            return
        vids = self.state.videos
        if any(has_work(it) for it in vids) and not messagebox.askyesno(
                "Edit Studio", f"Xoá hết {len(vids)} video khỏi hàng đợi?\n\nPhụ đề, bản dịch, giọng đã làm vẫn còn trong "
                               "thư mục output — nạp lại video sẽ được hỏi dùng lại."):
            return
        self.state.clear_videos()

    # ── Vẽ ────────────────────────────────────────────────────────────────
    def rebuild(self) -> None:
        """Đồng bộ danh sách dòng với hàng đợi: dùng lại dòng cũ, chỉ tạo/xoá phần chênh lệch."""
        vids = self.state.videos
        for r in self.rows[len(vids):]:
            r.destroy()
        del self.rows[len(vids):]
        self.count_right.configure(text=f"{len(vids)} video")
        if not vids:
            self.empty.grid(row=0, column=0, pady=20)
            return
        self.empty.grid_forget()
        for i, it in enumerate(vids):
            if i < len(self.rows):
                self.rows[i].assign(i, it)
                continue
            row = QueueRow(self.list, self, i, it)
            row.grid(row=i, column=0, sticky="ew", pady=2, padx=2)
            self.rows.append(row)

    def _highlight(self) -> None:
        for r in self.rows:
            r.update_view()

    def update_row(self, item: VideoItem) -> None:
        for r in self.rows:
            if r.item is item:
                r.update_view()
                return
