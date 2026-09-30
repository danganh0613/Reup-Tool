"""Thông tin phần cứng hiển thị trong UI (tên GPU…)."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def gpu_name() -> str:
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
