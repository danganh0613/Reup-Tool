"""Cổng lệnh cục bộ của app đang mở + phía gửi lệnh (CLI / MCP).

Giao thức: mỗi kết nối gửi MỘT dòng JSON ``{"token", "op", "args"}`` → nhận MỘT dòng JSON kết quả. Chỉ nghe ở
127.0.0.1, cổng ngẫu nhiên; cổng + mã ghi ở ``output/control.json`` (mã đổi mỗi lần mở app). Lệnh chạy trên luồng của
cổng (không chặn giao diện); phần đụng tới giao diện / trạng thái đi qua ``host.call`` (luồng Tk)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.config.paths import OUTPUT_DIR, ROOT

logger = logging.getLogger(__name__)

#: chỗ ghi cổng + mã (biến môi trường EDIT_STUDIO_CONTROL_FILE: bài test trỏ sang chỗ khác, không đè app thật)
CONTROL_FILE = Path(os.environ.get("EDIT_STUDIO_CONTROL_FILE") or OUTPUT_DIR / "control.json")
MAX_LINE = 4 << 20                                   # một lệnh / một kết quả tối đa 4 MB


class ControlServer:
    """Chạy trong app (MainWindow): nhận lệnh, gọi ``handler(op, args) -> dict``."""

    def __init__(self, handler: Callable[[str, dict], dict]) -> None:
        self.handler = handler
        self.token = secrets.token_hex(16)
        outer = self

        class _H(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    req = json.loads(self.rfile.readline(MAX_LINE).decode("utf-8") or "{}")
                    if not secrets.compare_digest(str(req.get("token", "")), outer.token):
                        res = {"error": "sai mã"}
                    else:
                        res = outer.handler(str(req.get("op", "")), dict(req.get("args") or {}))
                except Exception as exc:                 # noqa: BLE001 — lỗi lệnh trả về cho bên gọi, không làm sập app
                    logger.exception("Lệnh điều khiển lỗi")
                    res = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                self.wfile.write((json.dumps(res, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))

        class _S(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False

        self.server = _S(("127.0.0.1", 0), _H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name="Control", daemon=True)

    def start(self) -> None:
        self.thread.start()
        try:
            CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONTROL_FILE.write_text(json.dumps({"port": self.port, "token": self.token, "pid": os.getpid()}),
                                    encoding="utf-8")
        except OSError:
            logger.warning("Không ghi được %s", CONTROL_FILE, exc_info=True)
        logger.info("Cổng lệnh cục bộ: 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:                                # noqa: BLE001
            pass
        try:
            data = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                CONTROL_FILE.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


# ── Phía gửi lệnh ──────────────────────────────────────────────────────────
def send(op: str, args: dict | None = None, timeout: float = 3600) -> dict | None:
    """Gửi lệnh tới app đang mở. None = app chưa mở (không có cổng / không kết nối được)."""
    try:
        info = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
        port, token = int(info["port"]), str(info["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.settimeout(timeout)
            s.sendall((json.dumps({"token": token, "op": op, "args": args or {}}, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(1 << 16)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_LINE:
                    break
        return json.loads(buf.decode("utf-8") or "{}")
    except (OSError, ValueError):
        return None


def app_running() -> bool:
    """App đang mở (khoá «chỉ một cửa sổ» của main.py đang giữ) — Windows; nơi khác coi như không biết (False)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import hashlib

        name = "EditStudio_" + hashlib.sha1(str(ROOT).lower().encode("utf-8")).hexdigest()[:16]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenMutexW(0x00100000, False, name)        # SYNCHRONIZE
        if h:
            k32.CloseHandle(h)
            return True
    except Exception:                                    # noqa: BLE001
        pass
    return False


def launch_app() -> None:
    """Mở app (không chờ) — bản .exe mở chính nó, bản mã nguồn chạy main.py bằng python đang dùng."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        exe = Path(sys.executable)
        gui = exe.with_name("pythonw.exe")                # không bật cửa sổ console
        cmd = [str(gui if gui.exists() else exe), str(ROOT / "main.py")]
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=str(ROOT), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call(op: str, args: dict | None = None, *, start: bool = True, wait_open: float = 90) -> dict:
    """Gửi lệnh; app chưa mở thì (``start``) mở app rồi chờ cổng lệnh sẵn sàng."""
    res = send(op, args)
    if res is not None:
        return res
    if not start:
        return {"error": "Edit Studio chưa mở"}
    if app_running():                                    # đang mở mà không nhận lệnh: bản cũ / cổng lỗi → không mở thêm
        return {"error": "Edit Studio đang mở nhưng chưa nhận lệnh (bản cũ chưa có cổng lệnh) — đóng rồi mở lại app"}
    try:
        CONTROL_FILE.unlink(missing_ok=True)            # cổng cũ của lần mở trước (app đã đóng)
    except OSError:
        pass
    launch_app()
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_open:
        time.sleep(1.0)
        res = send(op, args)
        if res is not None:
            return res
    return {"error": f"Mở Edit Studio quá {wait_open:.0f} s mà chưa nhận lệnh"}
