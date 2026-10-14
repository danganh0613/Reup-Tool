"""Năm lệnh thô cho agent / CLI / MCP — chạy trong app đang mở (``GuiHost``), kết quả JSON gọn:

- ``videos(add?, preset?, limit?)`` — hàng đợi (id ngắn, trạng thái, số câu cần nghe lại); thêm video / thư mục, áp mẫu;
- ``run(ids?, action?, until?, wait_s?)`` — Chuẩn bị (dừng theo «Chạy đến») / xuất / chỉ chờ; chờ phía app tối đa wait_s;
- ``review(id, all?, limit?, offset?)`` — chỉ câu cần nghe lại (lý do ngắn) hoặc cả kịch bản, kèm cặp xưng hô của hồ sơ;
- ``fix(id, text?, request?, voice?, wait_s?)`` — sửa câu theo số câu hoặc nhờ AI của app sửa theo lời dặn + hồ sơ; tạo
  lại giọng;
- ``settings(…)`` — đúng các núm trọng tâm: mẫu, tỉ lệ, kiểu khung, canh, phóng to, dịch sang, lời dặn, chạy đến.

Không trả đường dẫn, log hay cả SRT — chữ dài cắt ngắn, danh sách có ``limit`` + ``more``."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.core.state import FIT_MODES, PREPARE_UNTIL, RATIOS, SIZE_MODES, VideoItem

RATIO_KEYS = {"goc": RATIOS[0], "21:9": RATIOS[1], "16:9": RATIOS[2], "4:3": RATIOS[3], "1:1": RATIOS[4],
              "3:4": RATIOS[5], "4:5": RATIOS[6], "9:16": RATIOS[7]}
FIT_KEYS = {"cat": FIT_MODES[0], "vien_den": FIT_MODES[1], "nen_mo": FIT_MODES[2]}
_RUNNING = ("Đang chuẩn bị", "Đang xử lý")


def vid(item: VideoItem) -> str:
    """Id ngắn ổn định theo đường dẫn video (không đổi khi hàng đợi thêm / bớt)."""
    return hashlib.sha1(str(Path(item.path)).lower().encode("utf-8")).hexdigest()[:6]


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


class GuiHost:
    """Lệnh chạy trên luồng của cổng lệnh; phần đụng giao diện / trạng thái đi qua ``call`` (luồng Tk)."""

    def __init__(self, window) -> None:
        self.w = window
        self.state = window.app_state

    def call(self, fn: Callable[[], object], timeout: float = 120) -> object:
        if threading.current_thread() is threading.main_thread():
            return fn()
        box: dict = {}
        ev = threading.Event()

        def run() -> None:
            try:
                box["v"] = fn()
            except BaseException as exc:                 # noqa: BLE001 — trả lỗi về luồng gọi
                box["e"] = exc
            finally:
                ev.set()

        self.w.after(0, run)
        if not ev.wait(timeout):
            raise TimeoutError("giao diện bận quá lâu")
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def busy(self) -> bool:
        return bool(self.call(lambda: self.w.runner.running or self.w.tasks.running))


# ── tiện ích ───────────────────────────────────────────────────────────────
def _find(host: GuiHost, ids) -> list[VideoItem]:
    vids = host.state.videos
    if ids in (None, "", "all", ["all"]):
        return list(vids)
    out = []
    for x in ([ids] if isinstance(ids, str) else ids):
        x = str(x).strip()
        it = next((v for v in vids if vid(v) == x or v.name == x or Path(v.path).stem == x), None)
        if it is None:
            raise LookupError(f"không thấy video «{x}» — gọi videos() để xem id")
        out.append(it)
    return out


def _flags(host: GuiHost, it: VideoItem) -> dict:
    try:
        return host.w.pipeline_for(it).review_flags(it)
    except Exception:                                    # noqa: BLE001 — chưa có phụ đề / file hỏng
        return {}


def _row(host: GuiHost, it: VideoItem) -> dict:
    r: dict = {"id": vid(it), "name": _clip(it.name, 60), "status": it.status or "Chưa chạy"}
    if it.review:
        r["review"] = {"pending": "chờ duyệt", "approved": "đã duyệt", "skipped": "bỏ qua"}.get(it.review, it.review)
    if it.status == "Lỗi" and it.message:
        r["error"] = _clip(it.message, 160)
    if it.status == "Xong" and it.output_path:
        r["out"] = Path(it.output_path).name
    n = len(_flags(host, it))
    if n:
        r["flags"] = n
    return r


def _files(it: VideoItem) -> tuple[str | None, str | None]:
    has = lambda p: bool(p) and Path(p).exists()          # noqa: E731
    return (it.srt_translated_path if has(it.srt_translated_path) else None,
            it.srt_path if has(it.srt_path) else None)


def _people(brief: str) -> str:
    """Dòng «Người nói» của hồ sơ (cặp xưng hô) — đủ để agent sửa đúng, không gửi cả hồ sơ."""
    out, on = [], False
    for ln in (brief or "").splitlines():
        t = ln.strip()
        if not t.startswith("-"):
            on = t.startswith("Người nói")
            continue
        if on:
            out.append(t[1:].strip())
    return _clip(" | ".join(out), 300)


# ── 1. videos ──────────────────────────────────────────────────────────────
def videos(host: GuiHost, add=None, preset: str | None = None, limit: int = 50) -> dict:
    from app.services import templates as TP
    from app.ui.queue_panel import VIDEO_EXTS

    def work() -> dict:
        st, w = host.state, host.w
        added: list[VideoItem] = []
        if add:
            paths: list[str] = []
            for p in ([add] if isinstance(add, str) else add):
                pp = Path(str(p).strip().strip('"'))
                if pp.is_dir():
                    paths += [str(x) for x in sorted(pp.iterdir()) if x.is_file() and x.suffix.lower() in VIDEO_EXTS]
                elif pp.is_file():
                    paths.append(str(pp))
            t = TP.get_template(preset) if preset else None
            if preset and t is None:
                raise LookupError(f"không có mẫu «{preset}» — có: {', '.join(x.name for x in TP.list_templates())}")
            added = st.add_videos(paths)
            for it in added:
                if t is not None:
                    TP.apply_to_item(st, it, t)
                w.queue_update(it)
            if added:
                st.emit("videos_changed")
                st.mark_dirty()
                w.log(f"Agent: nạp {len(added)} video" + (f" (mẫu «{t.name}»)" if t else ""))
        rows = [_row(host, it) for it in st.videos[: max(1, int(limit))]]
        out: dict = {"videos": rows, "busy": bool(w.runner.running or w.tasks.running)}
        if len(st.videos) > len(rows):
            out["more"] = len(st.videos) - len(rows)
        if add is not None:
            out["added"] = len(added)
        if not st.videos:
            out["hint"] = "hàng đợi trống — videos(add=[đường dẫn video hoặc thư mục])"
        return out

    return host.call(work)


# ── 2. run ─────────────────────────────────────────────────────────────────
def run(host: GuiHost, ids="all", action: str = "prepare", until: str | None = None, wait_s: int = 0) -> dict:
    if action not in ("prepare", "render", "wait"):
        raise ValueError("action: prepare | render | wait")
    if until is not None and until not in PREPARE_UNTIL:
        raise ValueError(f"until: {' | '.join(PREPARE_UNTIL)}")

    def start() -> tuple[str | None, list[str]]:
        w = host.w
        if until is not None:
            w._set_prepare_until(until)
        if action == "wait":
            return None, [vid(it) for it in _find(host, ids)]
        items = _find(host, ids)
        if action == "prepare":
            items = [it for it in items if it.review != "approved" and it.status not in _RUNNING]
            if not items:
                return "không còn video cần chuẩn bị (đã duyệt / đang chạy)", []
            for it in items:
                if it.status == "Lỗi":
                    it.review = ""
            return w._run_queue(items, mode="prepare", quiet=True), [vid(it) for it in items]
        ready = [it for it in items if it.status in ("Chờ duyệt", "Cần xuất lại", "Đã duyệt") or it.review == "approved"
                 or (it.status not in (*_RUNNING, "Lỗi") and w.pipeline_for(it).ready_for_review(it))]
        if not ready:
            return "chưa có video nào chuẩn bị xong để xuất — run(action='prepare') trước", []
        for it in ready:
            it.review = "approved"                      # agent được bảo xuất → coi như đã duyệt
        return w._run_queue(ready, mode="render", quiet=True), [vid(it) for it in ready]

    why, started = host.call(start)
    if why:
        return {"error": why}
    deadline = time.monotonic() + max(0, min(int(wait_s or 0), 3600))
    while time.monotonic() < deadline and host.busy():
        time.sleep(1.5)

    def result() -> dict:
        w = host.w
        busy = bool(w.runner.running or w.tasks.running)
        out: dict = {"busy": busy, "videos": [_row(host, it) for it in host.state.videos if vid(it) in started]}
        if action != "wait":
            out["started"] = len(started)
        if busy:
            out["hint"] = "đang chạy — run(action='wait', wait_s=…) để chờ tiếp"
        return out

    return host.call(result)


# ── 3. review ──────────────────────────────────────────────────────────────
def review(host: GuiHost, id: str, all: bool = False, limit: int = 20, offset: int = 0) -> dict:   # noqa: A002
    from app.services.srt_utils import load_srt

    def work() -> dict:
        it = _find(host, [id])[0]
        tr, src = _files(it)
        if not (tr or src):
            return {"id": vid(it), "error": "chưa có lời thoại — run(action='prepare') trước"}
        cues = load_srt(tr or src)
        ref = load_srt(src) if tr and src else []
        same = len(ref) == len(cues)
        flags = _flags(host, it)
        idx = list(range(len(cues))) if all else sorted(flags)
        page = idx[max(0, int(offset)): max(0, int(offset)) + max(1, int(limit))]
        lines = []
        for i in page:
            row: dict = {"n": i + 1, "t": _clip(cues[i].plain, 90)}
            if tr and same:
                row["src"] = _clip(ref[i].plain, 50)
            if i in flags:
                row["why"] = "; ".join(f.short for f in flags[i])
            lines.append(row)
        out: dict = {"id": vid(it), "kind": "bản dịch" if tr else "lời gốc", "total": len(cues), "flagged": len(flags),
                     "lines": lines}
        rest = len(idx) - int(offset) - len(page)
        if rest > 0:
            out["more"] = rest
        people = _people(host.w.pipeline_for(it).load_brief(it))
        if people and not offset:
            out["people"] = people
        return out

    return host.call(work)


# ── 4. fix ─────────────────────────────────────────────────────────────────
def fix(host: GuiHost, id: str, text: dict | None = None, request: str | None = None, voice: bool = False,   # noqa: A002
        wait_s: int = 300, auto: bool = False) -> dict:
    from app.services.srt_utils import load_srt, save_srt, wrap_lines

    def load():
        it = _find(host, [id])[0]
        tr, src = _files(it)
        if not (tr or src):
            raise LookupError("chưa có lời thoại — run(action='prepare') trước")
        cues = load_srt(tr or src)
        ref = load_srt(src) if tr and src else []
        return it, tr or src, bool(tr), cues, ref, host.state.state_for(it)

    it, path, is_tr, cues, ref, st = host.call(load)
    changed: set[int] = set()
    for k, v in (text or {}).items():
        n = int(k)
        new = " ".join(str(v).split())
        if 1 <= n <= len(cues) and new and new != cues[n - 1].plain.replace("\n", " ").strip():
            cues[n - 1].text = wrap_lines(new)
            changed.add(n)
    note = ""
    if request:
        from app.services.translate_router import ai_edit

        current = [c.plain.replace("\n", " ").strip() for c in cues]
        source = [c.plain for c in ref] if is_tr and len(ref) == len(cues) else None
        brief = host.w.pipeline_for(it).load_brief(it)
        edits, note = ai_edit(current, source, request, settings=st.settings, target=st.subtitle.translate_target,
                              style=st.subtitle.translate_style, context="", brief=brief, log=host.w.log_bg)
        for i, t in edits.items():
            t = " ".join((t or "").split())
            if 0 <= i < len(cues) and t and t != current[i]:
                cues[i].text = wrap_lines(t)
                changed.add(i + 1)
    skipped: list[int] = []
    if auto:                                             # như nút «Tự sửa»: AI sửa mọi câu ⚠ đúng lý do
        current = [c.plain.replace("\n", " ").strip() for c in cues]
        edits, skipped = host.w.pipeline_for(it).auto_fix(it, current, log=host.w.log_bg)
        for i, t in edits.items():
            t = " ".join((t or "").split())
            if 0 <= i < len(cues) and t and t != current[i]:
                cues[i].text = wrap_lines(t)
                changed.add(i + 1)

    def save() -> None:
        w = host.w
        save_srt(cues, path)
        if is_tr and ref:                                # câu agent / AI đã sửa: bước rút gọn câu tràn không ghi đè
            w.pipeline.add_line_locks(path, "edited", [ref[n - 1].plain for n in changed if n - 1 < len(ref)])
        if it.status == "Xong":
            it.status = "Cần xuất lại"
        w.queue_update(it)
        srt = w.modules.get("srt")
        if srt is not None and srt.editor.item is it:
            srt.editor.load(it)                          # tab Lời thoại đang mở video này → hiện chữ mới
        w.log(f"Agent: sửa {len(changed)} câu của {it.name}")

    if changed:
        host.call(save)
    out: dict = {"changed": sorted(changed)}
    if note:
        out["note"] = _clip(note, 200)
    if skipped:
        out["too_short"] = [i + 1 for i in skipped]     # khung quá ngắn: rút gọn kiểu gì cũng không vừa
    if voice:
        out["voice"] = _regen_voice(host, it, wait_s)
    out["flags"] = len(host.call(lambda: _flags(host, it)))
    return out


def _regen_voice(host: GuiHost, it: VideoItem, wait_s: int) -> str:
    """Tạo lại giọng (câu không đổi dùng lại) qua hàng tác vụ của app — không dừng ở «Chạy đến: Đến bản dịch»."""
    done = threading.Event()
    box: dict = {}

    def kick() -> str | None:
        w = host.w
        if w.runner.running or w.tasks.running:
            return "bận — đang chạy việc khác"
        keep = (it.review, it.status)
        pipe = w.pipeline_for(it)

        def work(cancel):
            pipe.prepare(it, on_update=lambda x: w.ui_call(lambda: w.queue_update(x)), log=w.log_bg, cancel=cancel,
                         stops=False)
            if keep[0] in ("approved", "skipped"):
                it.review, it.status = keep

        def fin(_r, error):
            box["error"] = error
            done.set()

        w.run_task("Tạo lại giọng (agent)", work, fin)
        return None

    why = host.call(kick)
    if why:
        return why
    if not done.wait(max(1, min(int(wait_s or 0), 3600))):
        return "đang tạo — run(action='wait') để chờ"
    return f"lỗi: {_clip(str(box['error']), 160)}" if box.get("error") else "xong"


# ── 5. settings ────────────────────────────────────────────────────────────
def settings(host: GuiHost, template: str | None = None, ratio: str | None = None, fit: str | None = None,
             offset_x: int | None = None, offset_y: int | None = None, zoom: int | None = None,
             target: str | None = None, style: str | None = None, until: str | None = None,
             size: str | None = None) -> dict:
    from app.core.state import TRANSLATE_TARGETS
    from app.services import templates as TP

    def work() -> dict:
        st, w = host.state, host.w
        if template:
            t = TP.get_template(template)
            if t is None:
                raise LookupError(f"không có mẫu «{template}» — có: {', '.join(x.name for x in TP.list_templates())}")
            TP.apply_shared(st, t)
            w.log(f"Agent: áp mẫu «{t.name}»")
        v, sub = st.video, st.subtitle
        if ratio is not None:
            if ratio not in RATIO_KEYS:
                raise ValueError(f"ratio: {' | '.join(RATIO_KEYS)}")
            v.ratio = RATIO_KEYS[ratio]
        if fit is not None:
            if fit not in FIT_KEYS:
                raise ValueError(f"fit: {' | '.join(FIT_KEYS)}")
            v.fit_mode = FIT_KEYS[fit]
        if offset_x is not None:
            v.offset_x = max(-50, min(50, int(offset_x)))
        if offset_y is not None:
            v.offset_y = max(-50, min(50, int(offset_y)))
        if zoom is not None:
            v.zoom = max(100, min(400, int(zoom)))
        if size is not None:
            if size not in SIZE_MODES:
                raise ValueError(f"size: {' | '.join(SIZE_MODES)}")
            v.size_mode = size
        if target is not None:
            if target not in TRANSLATE_TARGETS:
                raise ValueError(f"target: {' | '.join(TRANSLATE_TARGETS)}")
            sub.translate_target = target
        if style is not None:
            sub.translate_style = str(style).strip()
        if until is not None:
            if until not in PREPARE_UNTIL:
                raise ValueError(f"until: {' | '.join(PREPARE_UNTIL)}")
            w._set_prepare_until(until)
        if any(x is not None for x in (ratio, fit, offset_x, offset_y, zoom, size)):
            st.config_changed("video")
        if target is not None or style is not None:
            st.config_changed("subtitle")
        if any(x is not None for x in (template, ratio, fit, offset_x, offset_y, zoom, target, style, size)):
            w.refresh_modules()
            w.preview.schedule_refresh(0)
        rkey = next((k for k, x in RATIO_KEYS.items() if x == v.ratio), v.ratio)
        fkey = next((k for k, x in FIT_KEYS.items() if x == v.fit_mode), v.fit_mode)
        return {"template": st.template_name or "", "ratio": rkey, "fit": fkey, "offset_x": v.offset_x,
                "offset_y": v.offset_y, "zoom": v.zoom, "size": v.size_mode, "target": sub.translate_target,
                "style": _clip(sub.translate_style, 160), "until": st.settings.prepare_until,
                "templates": [t.name for t in TP.list_templates()]}

    return host.call(work)


OPS: dict[str, Callable[..., dict]] = {"videos": videos, "run": run, "review": review, "fix": fix, "settings": settings}


def dispatch(host: GuiHost, op: str, args: dict) -> dict:
    fn = OPS.get(op)
    if fn is None:
        return {"error": f"không có lệnh «{op}» — có: {', '.join(OPS)}"}
    try:
        return fn(host, **args)
    except TypeError as exc:                             # tham số sai tên
        return {"error": f"tham số sai: {exc}"[:200]}
    except (LookupError, ValueError, TimeoutError) as exc:
        return {"error": str(exc)[:300]}
