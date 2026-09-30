"""Thông báo Windows (toast) khi xong việc — dùng PowerShell, không cần thư viện."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

from app.config.paths import APP_NAME

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


def notify(title: str, body: str, *, enabled: bool = True) -> None:
    logger.info("Thông báo: %s — %s", title, body)
    if not enabled or sys.platform != "win32":
        return

    def run() -> None:
        try:
            script = _PS.replace("{title}", _escape(title)).replace("{body}", _escape(body)).replace("{app}", APP_NAME)
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                           capture_output=True, timeout=15)
        except Exception:
            logger.debug("Toast thất bại", exc_info=True)
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
