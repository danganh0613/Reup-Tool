"""Thông tin phần cứng hiển thị trong UI (tên GPU…)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from functools import lru_cache


def _mac_chip() -> str | None:
    """macOS: «Apple M2 Pro» (GPU nằm chung chip)."""
    try:
        out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        return out if out.startswith("Apple") else None
    except Exception:
        return None


@lru_cache(maxsize=1)
def gpu_name() -> str:
    if sys.platform == "darwin":
        return _mac_chip() or "GPU không xác định"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=8, creationflags=flags).stdout.strip()
        if out:
            name, mem = (out.splitlines()[0].split(",") + [""])[:2]
            mem = mem.strip()
            if mem.endswith("MiB"):
                try:
                    mem = f"{round(int(mem[:-3]) / 1024)}GB"
                except ValueError:
                    pass
            return f"{name.strip()} ({mem})" if mem else name.strip()
    except Exception:
        pass
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
                             capture_output=True, text=True, timeout=10, creationflags=flags).stdout.strip()
        if out:
            return out.splitlines()[0].strip()
    except Exception:
        pass
    return "GPU không xác định"


def gpu_name_nowait() -> str | None:
    """Tên GPU nếu đã dò xong, không thì None — dùng trên luồng UI (lần dò đầu chạy nvidia-smi/PowerShell, 1–10 giây)."""
    return gpu_name() if gpu_name.cache_info().currsize else None


def prewarm_gpu_name() -> None:
    """Dò tên GPU trong nền ngay khi khởi động để các màn hình sau lấy được ngay."""
    threading.Thread(target=gpu_name, name="gpu-name", daemon=True).start()
