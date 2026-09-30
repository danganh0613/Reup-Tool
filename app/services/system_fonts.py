"""Danh sách font hệ thống và tra file font theo tên family (cho drawtext của FFmpeg)."""

from __future__ import annotations

import os
import sys
import tkinter.font as tkfont
from pathlib import Path

_cache: list[str] | None = None
_registry: dict[str, str] | None = None
_PREFERRED = ["Arial", "Segoe UI", "Roboto", "Montserrat", "Open Sans", "Times New Roman", "Tahoma",
              "Verdana", "Calibri", "Impact", "Bahnschrift", "Consolas", "Georgia"]
_FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]


def list_fonts(root=None) -> list[str]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        fams = sorted({f for f in tkfont.families(root) if f and not f.startswith("@")})
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
    _registry = out
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
    for fallback in ("arial", "segoe ui", "tahoma"):
        if fallback in normalized:
            p = _resolve(normalized[fallback])
            if p:
                return p
    return None
