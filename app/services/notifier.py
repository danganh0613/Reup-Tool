"""Thông báo khi xong việc: Windows toast (PowerShell) · macOS Notification Center (osascript) — không cần thư viện."""

from __future__ import annotations

import logging
import subprocess
import threading

from app.config.paths import APP_NAME
from app.core.platform import IS_MAC, IS_WIN, NO_WINDOW, beep

logger = logging.getLogger(__name__)

_PS = r'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast><visual><binding template="ToastGeneric"><text>{title}</text><text>{body}</text></binding></visual></toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app}").Show($toast)
'''


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("$", "`$"))


def _osa_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, body: str, *, enabled: bool = True) -> None:
    logger.info("Thông báo: %s — %s", title, body)
    if not enabled or not (IS_WIN or IS_MAC):
        return

    def run() -> None:
        try:
            if IS_WIN:
                script = _PS.replace("{title}", _escape(title)).replace("{body}", _escape(body)).replace("{app}", APP_NAME)
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               creationflags=NO_WINDOW, capture_output=True, timeout=15)
            else:
                subprocess.run(["osascript", "-e", f"display notification {_osa_quote(body)} with title {_osa_quote(title)}"],
                               capture_output=True, timeout=15)
        except Exception:
            logger.debug("Thông báo hệ thống thất bại", exc_info=True)
        beep()

    threading.Thread(target=run, daemon=True).start()
