"""Mẫu dựng video: thanh chọn mẫu trên đầu cửa sổ, hộp «Lưu mẫu», cửa sổ «Quản lý mẫu»."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.platform import open_path
from app.core.state import TEMPLATE_SECTIONS
from app.services import templates as TP
from app.ui.widgets.common import EntryRow, StatusLabel, hint
from app.ui.widgets.ui import Select, button

APP = "Edit Studio"



def _approved_shared(state) -> list:
    """Video đã duyệt (chưa xuất) đang theo cấu hình chung — áp mẫu khác thì kết quả xuất đổi mà chưa xem lại."""
    return [it for it in state.videos if it.config is None and it.review == "approved"
            and it.status not in ("Xong", "Cần xuất lại")]


def _reset_approved(window, items: list) -> None:
    for it in items:
        it.review, it.status = "pending", "Chờ duyệt"
        window.queue_update(it)
    if items:
        window.log(f"{len(items)} video đã duyệt chuyển về «Chờ duyệt» vì cấu hình đổi — xem lại ở tab Duyệt.")

class TemplateBar(ctk.CTkFrame):
    """[icon] Mẫu: [chọn mẫu ▾] • đã chỉnh  [Lưu mẫu] [Quản lý] — đặt giữa thanh trên cùng."""

    def __init__(self, master, window) -> None:
        super().__init__(master, fg_color="transparent")
        self.window = window
        self.state = window.app_state
        self._check_id: str | None = None
        self.label = ctk.CTkLabel(self, text="Mẫu", font=T.font(12, "medium"), text_color=T.TEXT_DIM)
        self.label.pack(side="left", padx=(0, 6))
        self._compact = False
        self.select = Select(self, [TP.NO_TEMPLATE], value=TP.NO_TEMPLATE, command=self._chosen, width=290, height=34,
                             icon_name="grid", menu_width=380,
                             tip="Chọn mẫu dựng video — áp cho mọi video chưa chỉnh riêng")
        self.select.pack(side="left")
        self.dirty = ctk.CTkLabel(self, text="", font=T.font(11), text_color=T.WARNING)
        self.dirty.pack(side="left", padx=(6, 0))
        button(self, "Lưu mẫu", icon_name="save", size="sm", command=self.save,
               tip="Lưu thiết lập đang chỉnh thành mẫu để dùng lại cho các video sau").pack(side="left", padx=(8, 3))
        button(self, "", icon_name="list", size="sm", command=self.manage, tip="Quản lý mẫu: xem, áp, xuất / nhập, xoá").pack(
            side="left")
        self.state.on("config_changed", self._schedule_check)
        self.state.on("config_loaded", self.refresh)
        self.refresh()

    def destroy(self) -> None:
        self.state.off("config_changed", self._schedule_check)
        self.state.off("config_loaded", self.refresh)
        super().destroy()

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.select.configure(width=180 if compact else 290)
        if compact:
            self.label.pack_forget()
        else:
            self.label.pack(side="left", padx=(0, 6), before=self.select)

    # ── Hiển thị ─────────────────────────────────────────────────────────
    def refresh(self, *_a) -> None:
        names = [t.name for t in TP.list_templates()]
        self.select.set_values([TP.NO_TEMPLATE] + names)
        cur = self.state.template_name if self.state.template_name in names else TP.NO_TEMPLATE
        self.select.set(cur)
        self._check()

    def _schedule_check(self, *_a) -> None:
        if self._check_id is not None:
            try:
                self.after_cancel(self._check_id)
            except Exception:
                pass
        self._check_id = self.after(300, self._check)

    def _check(self) -> None:
        self._check_id = None
        act = self.state.active
        if act is not None and act.config is not None:
            self.dirty.configure(text="✎ video này chỉnh riêng", text_color=T.INFO)
            return
        t = TP.get_template(self.state.template_name) if self.state.template_name else None
        self.dirty.configure(text="• đã chỉnh" if TP.differs(self.state, t) else "", text_color=T.WARNING)

    # ── Hành động ────────────────────────────────────────────────────────
    def _chosen(self, name: str) -> None:
        if name == TP.NO_TEMPLATE:
            self.state.template_name = ""
            self.state.mark_dirty()
            self._check()
            return
        t = TP.get_template(name)
        if t is None:
            return
        cur = TP.get_template(self.state.template_name) if self.state.template_name else None
        # chưa dùng mẫu nào: chỉ hỏi khi người dùng đã tự chỉnh (cấu hình mặc định thì áp luôn)
        unsaved = TP.differs(self.state, cur) if cur is not None else bool(self.state.ui.get("edited"))
        act = self.state.active
        own = act is not None and act.config is not None
        approved = _approved_shared(self.state)
        note = (f"\n\n{len(approved)} video đã duyệt (theo cấu hình chung) sẽ về «Chờ duyệt» để xem lại."
                if approved else "")
        if own:
            note += "\n\nVideo đang xem có cấu hình riêng — giữ nguyên, mẫu chỉ áp cho các video còn lại."
        if (unsaved or approved or own) and not messagebox.askyesno(
                APP, f"Áp mẫu «{t.name}»?"
                     + (f"\n\nThiết lập đang chỉnh (chưa lưu thành mẫu) sẽ bị thay ở các phần: {t.parts_label()}.\n"
                        "Muốn giữ lại thì bấm «Không» rồi «Lưu mẫu» trước." if unsaved else "") + note,
                parent=self.window):
            self.select.set(self.state.template_name or TP.NO_TEMPLATE)
            return
        TP.apply_shared(self.state, t)
        _reset_approved(self.window, approved)
        self.window.log(f"Đã áp mẫu «{t.name}» cho các video chưa chỉnh riêng.")
        self.window.preview.schedule_refresh(0)

    def save(self) -> None:
        SaveTemplateDialog(self.window, on_saved=lambda _t: self.refresh())

    def manage(self) -> None:
        TemplateManager(self.window, on_changed=self.refresh)


class SaveTemplateDialog(ctk.CTkToplevel):
    def __init__(self, window, on_saved: Callable[[TP.Template], None] | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.app_state = window.app_state
        self.on_saved = on_saved
        self.title("Lưu mẫu")
        self.geometry("540x680+120+80")
        self.after(50, self.lift)
        self.transient(window)
        act = self.app_state.active
        own = act is not None and act.config is not None
        cur = (act.template if own else self.app_state.template_name) or ""
        body = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(body, text="Lưu thiết lập đang chỉnh thành mẫu", font=T.font(14, "bold"), anchor="w").pack(
            fill="x", padx=14, pady=(12, 2))
        hint(body, ("Đang chỉnh riêng video «" + act.name + "» — mẫu lấy thiết lập của video này.") if own else
             "Mẫu dùng lại cho các video sau: chọn ở ô «Mẫu» trên cùng, hoặc áp riêng từng video.").pack(fill="x", padx=14)
        self.name = EntryRow(body, "Tên mẫu", value="" if TP.is_builtin(cur) else cur,
                             placeholder="vd: Review phim kênh A")
        self.name.pack(fill="x", padx=14, pady=(10, 3))
        self.desc = EntryRow(body, "Ghi chú", placeholder="vd: 9:16, sub vàng, giọng NamMinh")
        self.desc.pack(fill="x", padx=14, pady=3)
        ctk.CTkLabel(body, text="Lưu những phần nào", font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w").pack(
            fill="x", padx=14, pady=(10, 2))
        self.parts: dict[str, ctk.BooleanVar] = {}
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x", padx=14)
        for i, (k, label) in enumerate(TEMPLATE_SECTIONS.items()):
            var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(grid, text=label, variable=var, font=T.font(12)).grid(row=i // 2, column=i % 2, sticky="w",
                                                                                padx=(0, 12), pady=3)
            self.parts[k] = var
        hint(body, "Phần bỏ tick sẽ giữ nguyên khi áp mẫu. Mẫu chỉ lưu cắt đầu / cuối (không lưu nhiều đoạn), và không "
                   "lưu cách lấy lời (Giọng nói / Chữ in sẵn — chọn ở tab STT).").pack(fill="x", padx=14, pady=(4, 0))
        self._order_voices()
        tts = self.app_state.sections_dict().get("tts", {})
        if "tts" in self.parts and tts.get("enable_tts"):
            ctk.CTkLabel(body, text="Giọng lồng tiếng lưu vào mẫu", font=T.font(12, "medium"), text_color=T.TEXT_DIM,
                         anchor="w").pack(fill="x", padx=14, pady=(10, 2))
            self.voices = ctk.CTkLabel(body, text=TP.voice_summary(tts), font=T.font(12), text_color=T.TEXT,
                                       anchor="w", justify="left", wraplength=470)
            self.voices.pack(fill="x", padx=14)
            if tts.get("multi_voice"):
                hint(body, "Gán theo thứ tự người nói; dư người thì tự chọn thêm giọng Nam / Nữ.").pack(fill="x", padx=14,
                                                                                              pady=(2, 0))
        self.thumb = ctk.BooleanVar(value=self.window.preview._frame is not None)
        ctk.CTkCheckBox(body, text="Dùng khung xem trước hiện tại làm ảnh đại diện", variable=self.thumb,
                        font=T.font(12)).pack(anchor="w", padx=14, pady=(10, 0))
        self.status = StatusLabel(body, "")
        self.status.pack(fill="x", padx=14, pady=(8, 0))
        foot = ctk.CTkFrame(body, fg_color="transparent")
        foot.pack(fill="x", side="bottom", padx=14, pady=12)
        button(foot, "Lưu mẫu", icon_name="save", variant="primary", command=self._save).pack(side="right")
        ctk.CTkButton(foot, text="Huỷ", width=80, height=36, command=self.destroy, **T.secondary_button()).pack(
            side="right", padx=8)

    def _order_voices(self) -> None:
        """Danh sách giọng của mẫu = đúng thứ tự người nói của video đang xem (người 1 → giọng 1, …); giọng dư
        (của mẫu cũ, cho video nhiều người hơn) giữ ở cuối danh sách."""
        from app.services.diarize import resolve_voices

        act = self.app_state.active
        cfg = self.app_state.tts
        try:
            smap = self.window.pipeline_for(act).speaker_map(act) if act is not None else None
        except Exception:
            smap = None
        if smap is None or not cfg.multi_voice:
            return
        ordered = resolve_voices(cfg.speaker_voices, smap)
        taken = {e["name"] for e in ordered}
        extra = [sv for sv in cfg.speaker_voices
                 if isinstance(sv, dict) and sv.get("voice_id") and sv.get("name") not in taken]
        cfg.speaker_voices = ordered + extra

    def _save(self) -> None:
        name = self.name.get().strip()
        if not name:
            self.status.error("Nhập tên mẫu.")
            return
        parts = [k for k, v in self.parts.items() if v.get()]
        exists = any(t.name == name and not t.builtin for t in TP.list_templates())
        if exists and not messagebox.askyesno(APP, f"Đã có mẫu «{name}». Ghi đè?", parent=self):
            return
        try:
            t = TP.save_template(self.app_state, name, parts, self.desc.get().strip(),
                                 thumb=self.window.preview._frame if self.thumb.get() else None)
        except ValueError as exc:
            self.status.error(str(exc))
            return
        act = self.app_state.active
        if act is not None and act.config is not None:
            act.template = t.name
        else:
            self.app_state.template_name = t.name
        self.app_state.mark_dirty()
        self.window.log(f"Đã lưu mẫu «{t.name}» ({t.parts_label()}).")
        if self.on_saved:
            self.on_saved(t)
        self.window.queue_update(act) if act is not None else None
        self.destroy()


class TemplateManager(ctk.CTkToplevel):
    """Danh sách mẫu: ảnh · tên · mô tả · các phần — Áp cho tất cả / video đang chọn · Xuất · Xoá; Nhập mẫu."""

    def __init__(self, window, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.app_state = window.app_state
        self.on_changed = on_changed
        self.title("Quản lý mẫu")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(980, sw - 80)}x{min(720, sh - 120)}+80+60")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ctk.CTkLabel(head, text="Mẫu dựng video", font=T.font(14, "bold")).pack(side="left", padx=12, pady=10)
        button(head, "Mở thư mục mẫu", icon_name="openfolder", size="sm",
               command=lambda: (TP.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True), open_path(TP.TEMPLATES_DIR))).pack(
            side="right", padx=(4, 10))
        button(head, "Nhập mẫu…", icon_name="download", size="sm", command=self._import).pack(side="right", padx=4)
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.list.grid_columnconfigure(1, weight=1)
        self._images: list = []
        self.fill()

    def fill(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        self._images.clear()
        act = self.app_state.active
        for r, t in enumerate(TP.list_templates()):
            card = ctk.CTkFrame(self.list, fg_color=T.BG_CARD, corner_radius=10)
            card.grid(row=r, column=0, columnspan=2, sticky="ew", pady=4)
            card.grid_columnconfigure(1, weight=1)
            img = ctk.CTkImage(TP.thumbnail_for(t, 96), size=(72, 72))
            self._images.append(img)
            ctk.CTkLabel(card, text="", image=img).grid(row=0, column=0, rowspan=3, padx=10, pady=10, sticky="n")
            using = t.name == self.app_state.template_name
            ctk.CTkLabel(card, text=t.name + ("   ✓ đang dùng" if using else ""), font=T.font(13, "bold"),
                         text_color=T.ACCENT if using else T.TEXT, anchor="w").grid(row=0, column=1, sticky="ew", pady=(10, 0))
            ctk.CTkLabel(card, text=t.description or "—", font=T.font(11), text_color=T.TEXT_DIM, anchor="w", justify="left",
                         wraplength=560).grid(row=1, column=1, sticky="ew")
            ctk.CTkLabel(card, text=("Mẫu dựng sẵn · " if t.builtin else "Mẫu của bạn · ") + "Gồm: " + t.parts_label(),
                         font=T.font(10), text_color=T.TEXT_MUTED, anchor="w").grid(row=2, column=1, sticky="ew", pady=(0, 10))
            btns = ctk.CTkFrame(card, fg_color="transparent")
            btns.grid(row=0, column=2, rowspan=3, padx=10)
            button(btns, "Áp cho tất cả", icon_name="check", variant="primary", size="sm",
                   command=lambda t=t: self._apply_all(t),
                   tip="Áp vào cấu hình chung — mọi video chưa chỉnh riêng").pack(fill="x", pady=2)
            b = button(btns, "Chỉ video đang chọn", size="sm", command=lambda t=t: self._apply_one(t),
                       tip="Video đang chọn dùng mẫu này riêng, các video khác giữ nguyên")
            b.pack(fill="x", pady=2)
            if act is None:
                b.configure(state="disabled")
            row = ctk.CTkFrame(btns, fg_color="transparent")
            row.pack(fill="x", pady=2)
            button(row, "Xuất", icon_name="export", size="sm", command=lambda t=t: self._export(t)).pack(side="left")
            if not t.builtin:
                button(row, "", icon_name="trash", variant="destructive", size="sm", command=lambda t=t: self._delete(t),
                       tip="Xoá mẫu").pack(side="left", padx=(4, 0))

    def _changed(self) -> None:
        if self.on_changed:
            self.on_changed()
        self.fill()

    def _apply_all(self, t: TP.Template) -> None:
        approved = _approved_shared(self.app_state)
        if (self.app_state.ui.get("edited") or approved) and not messagebox.askyesno(
                APP, f"Áp mẫu «{t.name}» cho mọi video chưa chỉnh riêng?"
                     + ("\n\nThiết lập đang chỉnh (chưa lưu thành mẫu) sẽ bị thay." if self.app_state.ui.get("edited")
                        else "")
                     + (f"\n\n{len(approved)} video đã duyệt sẽ về «Chờ duyệt» để xem lại." if approved else ""),
                parent=self):
            return
        TP.apply_shared(self.app_state, t)
        _reset_approved(self.window, approved)
        self.window.log(f"Đã áp mẫu «{t.name}» cho các video chưa chỉnh riêng.")
        self.window.preview.schedule_refresh(0)
        self._changed()

    def _apply_one(self, t: TP.Template) -> None:
        item = self.app_state.active
        if item is None:
            return
        TP.apply_to_item(self.app_state, item, t)
        self.window.queue_update(item)
        self.window.log(f"{item.name}: dùng mẫu riêng «{t.name}».")
        self.window.preview.schedule_refresh(0)
        self._changed()

    def _export(self, t: TP.Template) -> None:
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile=f"{TP._safe(t.name)}.json",
                                         filetypes=[("Mẫu Edit Studio", "*.json")])
        if p:
            TP.export_template(t, Path(p))
            self.window.log(f"Đã xuất mẫu «{t.name}» → {p}")

    def _import(self) -> None:
        p = filedialog.askopenfilename(parent=self, filetypes=[("Mẫu Edit Studio", "*.json"), ("Tất cả", "*.*")])
        if not p:
            return
        try:
            t = TP.import_template(Path(p))
        except Exception as exc:
            messagebox.showerror(APP, f"Không nhập được mẫu: {exc}", parent=self)
            return
        self.window.log(f"Đã nhập mẫu «{t.name}».")
        self._changed()

    def _delete(self, t: TP.Template) -> None:
        if not messagebox.askyesno(APP, f"Xoá mẫu «{t.name}»?", parent=self):
            return
        TP.delete_template(t.name)
        if self.app_state.template_name == t.name:
            self.app_state.template_name = ""
            self.app_state.mark_dirty()
        self._changed()
