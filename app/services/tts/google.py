"""Google Cloud Text-to-Speech qua REST API chính thức (API key)."""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

import requests

from app.services.tts.base import TTSEngine, Voice, part_path

logger = logging.getLogger(__name__)

_BASE = "https://texttospeech.googleapis.com/v1"
PREFERRED_LANGS = ["vi-VN", "en-US", "en-GB", "en-AU", "ja-JP", "ko-KR", "cmn-CN", "fr-FR", "de-DE", "es-ES",
                   "th-TH", "id-ID", "ru-RU"]


class GoogleTTS(TTSEngine):
    name = "google"
    native_speed_range = (0.25, 4.0)
    workers = 4

    def __init__(self, api_key: str | list[str], sa_json: str = "") -> None:
        keys = [api_key] if isinstance(api_key, str) else list(api_key or [])
        self.keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        self._key_idx = 0
        self.api_key = self.keys[0] if self.keys else ""
        self.sa_json = (sa_json or "").strip()
        self._token: str | None = None
        self._token_exp = 0.0
        self._voices: list[Voice] | None = None

    def _rotate_key(self) -> bool:
        """Đổi sang key kế tiếp khi hết hạn mức / key bị từ chối. False nếu chỉ có một key."""
        if len(self.keys) < 2:
            return False
        self._key_idx = (self._key_idx + 1) % len(self.keys)
        self.api_key = self.keys[self._key_idx]
        logger.warning("Google TTS: chuyển sang API key #%d/%d", self._key_idx + 1, len(self.keys))
        return True

    # ── Xác thực ─────────────────────────────────────────────────────────
    def _check(self) -> None:
        if not self.api_key and not self.sa_json:
            raise RuntimeError("Chưa có Google Cloud API key hoặc Service Account JSON (vào Cài đặt để nhập).")

    def _auth(self) -> tuple[dict, dict]:
        """(params, headers) — ưu tiên Service Account JSON (cần gói google-auth), không thì API key."""
        if self.sa_json and Path(self.sa_json).exists():
            if self._token and time.time() < self._token_exp - 60:
                return {}, {"Authorization": f"Bearer {self._token}"}
            try:
                from google.auth.transport.requests import Request
                from google.oauth2 import service_account
            except Exception as exc:
                if self.api_key:
                    logger.warning("Không có google-auth (%s) — dùng API key", exc)
                    return {"key": self.api_key}, {}
                raise RuntimeError("Dùng Service Account JSON cần cài: pip install google-auth") from exc
            creds = service_account.Credentials.from_service_account_file(
                self.sa_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(Request())
            self._token = creds.token
            self._token_exp = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
            return {}, {"Authorization": f"Bearer {self._token}"}
        return {"key": self.api_key}, {}

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        if self._voices is None:
            self._check()
            for _ in range(max(1, len(self.keys))):          # key bị từ chối → thử key kế tiếp
                params, headers = self._auth()
                r = requests.get(f"{_BASE}/voices", params=params, headers=headers, timeout=30)
                if r.status_code in (403, 429) and "key" in params and self._rotate_key():
                    continue
                break
            if r.status_code != 200:
                raise RuntimeError(f"Google TTS {r.status_code}: {r.text[:200]}")
            voices: list[Voice] = []
            for v in r.json().get("voices", []):
                langs = v.get("languageCodes") or [""]
                name = v.get("name", "")
                gender = {"MALE": "Male", "FEMALE": "Female"}.get(v.get("ssmlGender", ""), "")
                voices.append(Voice(id=name, name=name, locale=langs[0], gender=gender,
                                    extra={"rate": v.get("naturalSampleRateHertz", 24000)}))
            voices.sort(key=lambda x: (PREFERRED_LANGS.index(x.locale) if x.locale in PREFERRED_LANGS else 99,
                                       x.locale, x.name))
            self._voices = voices
        if locale:
            return [v for v in self._voices if v.locale == locale]
        return list(self._voices)

    def locales(self) -> list[str]:
        seen: dict[str, None] = {}
        for v in self.list_voices():
            seen.setdefault(v.locale, None)
        return list(seen)

    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        self._check()
        if not voice_id:
            raise RuntimeError("Chưa chọn giọng Google.")
        lang = "-".join(voice_id.split("-")[:2]) if voice_id.count("-") >= 2 else "vi-VN"
        audio_cfg: dict = {"audioEncoding": "MP3", "speakingRate": round(self.clamp_speed(speed), 2)}
        # Giọng Chirp/Studio không nhận pitch — chỉ gửi khi khác 0 và giọng hỗ trợ
        if pitch and "Chirp" not in voice_id and "Studio" not in voice_id:
            audio_cfg["pitch"] = max(-20.0, min(20.0, pitch / 5.0))
        body = {"input": {"text": text}, "voice": {"languageCode": lang, "name": voice_id}, "audioConfig": audio_cfg}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for attempt in range(4):
            try:
                params, headers = self._auth()
                r = requests.post(f"{_BASE}/text:synthesize", params=params, headers=headers, json=body, timeout=90)
                if r.status_code in (403, 429) and "key" in params and self._rotate_key():
                    continue                                   # hết hạn mức / key bị từ chối → key kế tiếp, thử ngay
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"Google TTS {r.status_code}: {r.text[:300]}")
                content = r.json().get("audioContent", "")
                if not content:
                    raise RuntimeError("Google TTS trả về rỗng")
                tmp = part_path(out_path)
                tmp.write_bytes(base64.b64decode(content))
                tmp.replace(out_path)
                return out_path
            except requests.RequestException as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Google TTS thất bại: {last}")
