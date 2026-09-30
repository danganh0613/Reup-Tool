"""ElevenLabs TTS qua REST API chính thức."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from app.services.tts.base import TTSEngine, Voice, part_path

logger = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsTTS(TTSEngine):
    name = "elevenlabs"
    native_speed_range = (0.7, 1.2)     # giới hạn của voice_settings.speed
    workers = 2

    def __init__(self, api_key: str, *, model_id: str = "eleven_multilingual_v2", stability: float = 0.5,
                 similarity: float = 0.75, style: float = 0.0, speaker_boost: bool = True,
                 extra_keys: list[str] | None = None) -> None:
        self.keys = [k.strip() for k in ([api_key] + list(extra_keys or [])) if k and k.strip()]
        self.keys = list(dict.fromkeys(self.keys))          # bỏ trùng, giữ thứ tự
        self._key_idx = 0
        self.api_key = self.keys[0] if self.keys else ""
        self.model_id = model_id
        self.stability = stability
        self.similarity = similarity
        self.style = style
        self.speaker_boost = speaker_boost
        self._voices: list[Voice] | None = None

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("Chưa có ElevenLabs API key (vào Cài đặt để nhập).")
        return {"xi-api-key": self.api_key, "accept": "application/json"}

    def _rotate_key(self) -> bool:
        """Chuyển sang key kế tiếp khi hết quota / key lỗi. Trả về False nếu đã thử hết."""
        if self._key_idx + 1 >= len(self.keys):
            return False
        self._key_idx += 1
        self.api_key = self.keys[self._key_idx]
        logger.warning("ElevenLabs: chuyển sang API key #%d", self._key_idx + 1)
        return True

    def cache_signature(self, voice_id: str, speed: float, pitch: int) -> str:
        return (f"{self.name}|{voice_id}|{self.model_id}|{self.clamp_speed(speed):.3f}|"
                f"{self.stability:.2f}|{self.similarity:.2f}|{self.style:.2f}|{int(self.speaker_boost)}")

    # ── Voices ────────────────────────────────────────────────────────────
    def list_voices(self, locale: str | None = None) -> list[Voice]:
        if self._voices is None:
            r = requests.get(f"{_BASE}/voices", headers=self._headers(), timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
            voices: list[Voice] = []
            for v in r.json().get("voices", []):
                labels = v.get("labels") or {}
                voices.append(Voice(id=v["voice_id"], name=v.get("name", v["voice_id"]),
                                    locale=labels.get("language", "") or labels.get("accent", ""),
                                    gender=labels.get("gender", ""),
                                    extra={"category": v.get("category", ""), "preview_url": v.get("preview_url", "")}))
            voices.sort(key=lambda x: (x.extra.get("category") != "cloned", x.name))
            self._voices = voices
        return list(self._voices)

    def subscription(self) -> dict:
        r = requests.get(f"{_BASE}/user/subscription", headers=self._headers(), timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
        d = r.json()
        return {"used": d.get("character_count", 0), "limit": d.get("character_limit", 0),
                "tier": d.get("tier", ""), "reset": d.get("next_character_count_reset_unix", 0)}

    # ── Synthesize ────────────────────────────────────────────────────────
    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        if not voice_id:
            raise RuntimeError("Chưa chọn giọng ElevenLabs.")
        body = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity,
                "style": self.style,
                "use_speaker_boost": self.speaker_boost,
                "speed": round(self.clamp_speed(speed), 2),
            },
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = part_path(out_path)
        last: Exception | None = None
        for attempt in range(4 + len(self.keys)):
            try:
                r = requests.post(f"{_BASE}/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                                  headers={**self._headers(), "content-type": "application/json"},
                                  json=body, timeout=120)
                if r.status_code in (401, 402) or (r.status_code == 429 and "quota" in r.text.lower()):
                    if self._rotate_key():
                        continue
                    raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
                tmp.write_bytes(r.content)
                tmp.replace(out_path)
                return out_path
            except requests.RequestException as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"ElevenLabs thất bại: {last}")
