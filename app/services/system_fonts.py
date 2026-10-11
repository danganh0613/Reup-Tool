"""Danh sách font hệ thống và tra file font theo tên family (cho drawtext của FFmpeg)."""

from __future__ import annotations

import os
import re
import sys
import tkinter.font as tkfont
from pathlib import Path

_cache: list[str] | None = None
_registry: dict[str, str] | None = None
_PREFERRED = ["Arial", "Segoe UI", "Roboto", "Montserrat", "Open Sans", "Times New Roman", "Tahoma",
              "Verdana", "Calibri", "Impact", "Bahnschrift", "Consolas", "Georgia"]
if sys.platform == "win32":
    _FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
elif sys.platform == "darwin":
    _FONT_DIRS = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"),
                  Path.home() / "Library" / "Fonts"]
else:
    _FONT_DIRS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local" / "share" / "fonts",
                  Path.home() / ".fonts"]


_SYMBOL = re.compile(r"wingdings|webdings|symbol|marlett|mt extra|font ?awesome|mdl2|fluent icons|material icons|"
                     r"icomoon|dingbat|emoji|bookshelf", re.I)


def list_fonts(root=None) -> list[str]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        fams = sorted({f for f in tkfont.families(root) if f and not f.startswith("@") and not _SYMBOL.search(f)})
    except Exception:
        fams = []
    preferred = [f for f in _PREFERRED if f in fams]
    rest = [f for f in fams if f not in preferred]
    _cache = preferred + rest
    return _cache


def _load_registry() -> dict[str, str]:
    """{"Arial Bold (TrueType)": "arialbd.ttf", …} từ registry Windows (máy + người dùng)."""
    global _registry
    if _registry is not None:
        return _registry
    out: dict[str, str] = {}
    if sys.platform == "win32":
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                out[name] = str(val)
                i += 1
    else:
        out = _scan_font_files()
    _registry = out
    return out


def _scan_font_files() -> dict[str, str]:
    """macOS/Linux không có registry font → đọc tên (family + style) trong từng file .ttf/.otf/.ttc."""
    from PIL import ImageFont

    out: dict[str, str] = {}
    for d in _FONT_DIRS:
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            ext = f.suffix.lower()
            if ext not in (".ttf", ".otf", ".ttc"):
                continue
            for index in range(8 if ext == ".ttc" else 1):       # .ttc chứa nhiều kiểu (Regular, Bold…)
                try:
                    fam, style = ImageFont.truetype(str(f), 12, index=index).getname()
                except Exception:
                    break
                if not fam:
                    continue
                key = fam if (style or "").lower() in ("", "regular", "normal", "roman") else f"{fam} {style}"
                out.setdefault(key, str(f))
    return out


def _resolve(val: str) -> Path | None:
    p = Path(val)
    if p.is_absolute() and p.exists():
        return p
    for d in _FONT_DIRS:
        cand = d / val
        if cand.exists():
            return cand
    return None


def font_file(family: str, *, bold: bool = False, italic: bool = False) -> Path | None:
    """Tìm file .ttf/.otf cho ``family`` (gần đúng theo tên đăng ký), fallback Arial."""
    reg = _load_registry()
    fam = (family or "Arial").strip().lower()
    want = []
    if bold and italic:
        want.append(f"{fam} bold italic")
    if bold:
        want.append(f"{fam} bold")
    if italic:
        want.append(f"{fam} italic")
    want.append(fam)
    # tên đăng ký dạng "Arial Bold (TrueType)" hoặc "Segoe UI Semibold (TrueType)"
    normalized = {k.lower().replace(" (truetype)", "").replace(" (opentype)", ""): v for k, v in reg.items()}
    for w in want:
        if w in normalized:
            p = _resolve(normalized[w])
            if p:
                return p
    # gần đúng: tên bắt đầu bằng family
    for k, v in normalized.items():
        if k.startswith(fam):
            p = _resolve(v)
            if p:
                return p
    for fallback in ("arial", "segoe ui", "tahoma", "helvetica neue", "helvetica", "dejavu sans"):
        if fallback in normalized:
            p = _resolve(normalized[fallback])
            if p:
                return p
    return None
