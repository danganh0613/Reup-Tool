"""FPT.AI Text to Speech (v5) — giọng Việt 3 miền, hay dùng cho review phim.

Gửi văn bản → nhận link MP3 «async»; file có sau vài giây (tới ~2 phút với câu dài) → hỏi lại tới khi tải được.
Key lấy ở console.fpt.ai (có lượt dùng thử); nhập nhiều key mỗi dòng một key, hết lượt tự chuyển key.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from app.services.tts.base import TTSEngine, Voice, part_path

logger = logging.getLogger(__name__)

URL = "https://api.fpt.ai/hmi/tts/v5"

VOICES: list[tuple[str, str, str]] = [
    ("banmai", "Ban Mai — miền Bắc", "Female"),
    ("thuminh", "Thu Minh — miền Bắc", "Female"),
    ("leminh", "Lê Minh — miền Bắc", "Male"),
    ("myan", "Mỹ An — miền Trung", "Female"),
    ("giahuy", "Gia Huy — miền Trung", "Male"),
    ("lannhi", "Lan Nhi — miền Nam", "Female"),
]


class FptTTS(TTSEngine):
    name = "fpt"
    ext = ".mp3"
    native_speed_range = (1.0, 1.0)       # tốc độ chỉnh bằng atempo (thang -3..3 của FPT quá thô)
    workers = 3

    def __init__(self, keys: list[str]) -> None:
        self.keys = [k for k in dict.fromkeys(k.strip() for k in keys) if k]
        self._key_idx = 0

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        return [Voice(id=v, name=label, locale="vi-VN", gender=g) for v, label, g in VOICES]

    def _key(self) -> str:
        if not self.keys:
            raise RuntimeError("Chưa có FPT.AI API key — lấy ở console.fpt.ai rồi dán vào Cài đặt → Giọng đọc.")
        return self.keys[self._key_idx % len(self.keys)]

    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        text = text.strip()
        if len(text) < 3:                      # API đòi tối thiểu 3 ký tự
            text = (text + " ...")[:max(3, len(text) + 4)]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        last = ""
        tried = 0
        for attempt in range(4 + 2 * max(1, len(self.keys))):
            try:
                r = requests.post(URL, data=text.encode("utf-8"), timeout=60,
                                  headers={"api_key": self._key(), "voice": voice_id or "banmai", "speed": "",
                                           "format": "mp3"})
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            except (requests.RequestException, ValueError) as exc:
                last = str(exc)
                time.sleep(1.5 * (attempt + 1))
                continue
            link = data.get("async")
            if r.status_code == 200 and int(data.get("error", 0) or 0) == 0 and link:
                return self._download(link, out_path)
            last = f"{r.status_code}: {data.get('message') or r.text[:200]}"
            if r.status_code in (401, 403, 429) or "quota" in last.lower() or "limit" in last.lower():
                tried += 1
                self._key_idx += 1
                if tried >= max(1, len(self.keys)):
                    raise RuntimeError(f"FPT.AI hết lượt / key lỗi ({last}).")
                continue
            if r.status_code >= 500:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"FPT.AI lỗi {last}")
        raise RuntimeError(f"FPT.AI thất bại: {last}")

    @staticmethod
    def _download(link: str, out_path: Path) -> Path:
        """File MP3 chưa có ngay → hỏi lại (tối đa ~2 phút)."""
        tmp = part_path(out_path)
        deadline = time.time() + 150
        wait = 1.0
        while time.time() < deadline:
            try:
                r = requests.get(link, timeout=60)
                if r.status_code == 200 and len(r.content) > 500:
                    tmp.write_bytes(r.content)
                    tmp.replace(out_path)
                    return out_path
            except requests.RequestException:
                pass
            time.sleep(wait)
            wait = min(5.0, wait * 1.5)
        raise RuntimeError("FPT.AI: quá 2 phút chưa có file âm thanh — thử lại sau.")
