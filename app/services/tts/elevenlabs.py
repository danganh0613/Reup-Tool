"""ElevenLabs TTS qua REST API chính thức."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import requests

from app.services.tts.base import TTSEngine, Voice, part_path

logger = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io/v1"
#: nhãn của thư viện giọng ElevenLabs → tiếng Việt
_VI_LABELS = {"northern": "miền Bắc", "southern": "miền Nam", "central": "miền Trung", "standard": "chuẩn",
              "narrative_story": "kể chuyện", "social_media": "mạng xã hội", "conversational": "trò chuyện",
              "characters_animation": "nhân vật hoạt hình", "informative_educational": "thuyết minh",
              "entertainment_tv": "giải trí / TV", "advertisement": "quảng cáo", "news": "tin tức",
              "warm": "ấm", "calm": "điềm tĩnh", "deep": "trầm", "young": "trẻ", "middle_aged": "trung niên",
              "old": "lớn tuổi", "confident": "tự tin", "casual": "tự nhiên", "professional": "chuyên nghiệp",
              "soft": "nhẹ", "energetic": "sôi nổi", "pleasant": "dễ nghe", "mature": "chín chắn"}
_ADDED: set[str] = set()                 # giọng cộng đồng đã thêm vào tài khoản trong phiên này
_ADD_LOCK = threading.Lock()


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
            own = {v.id for v in voices}
            try:
                voices += [v for v in self.community_voices("vi") if v.id.split(":")[-1] not in own]
            except Exception as exc:              # thư viện cộng đồng lỗi → vẫn dùng giọng của tài khoản
                logger.info("ElevenLabs: không tải được thư viện giọng cộng đồng: %s", exc)
            self._voices = voices
        return list(self._voices)

    def community_voices(self, language: str = "vi", pages: int = 3) -> list[Voice]:
        """Giọng người dùng ElevenLabs chia sẻ (Voice Library) theo ngôn ngữ — nhiều giọng Việt review phim, kể chuyện…

        Mã giọng dạng ``lib:<public_owner_id>:<voice_id>``; lần đầu đọc sẽ tự thêm giọng vào tài khoản."""
        out: list[Voice] = []
        for page in range(pages):
            r = requests.get(f"{_BASE}/shared-voices", headers=self._headers(), timeout=30,
                             params={"language": language, "page_size": 100, "page": page})
            if r.status_code != 200:
                if not out:
                    raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
                break
            data = r.json()
            for v in data.get("voices", []):
                vid, owner = v.get("voice_id"), v.get("public_owner_id")
                if not vid or not owner:
                    continue
                bits = [_VI_LABELS.get(str(b).lower(), str(b).replace("_", " "))
                        for b in (v.get("accent"), v.get("descriptive"), v.get("use_case")) if b]
                uses = v.get("cloned_by_count") or 0
                label = f"🌐 {v.get('name', vid)}" + (f" — {', '.join(bits)}" if bits else "") + (f" · {uses:,} lượt dùng" if uses else "")
                out.append(Voice(id=f"lib:{owner}:{vid}", name=label, locale=v.get("locale") or language,
                                 gender=v.get("gender", ""),
                                 extra={"category": "library", "preview_url": v.get("preview_url", ""), "uses": uses}))
            if not data.get("has_more"):
                break
        out.sort(key=lambda x: -int(x.extra.get("uses") or 0))
        return out

    def _ensure_library_voice(self, voice_id: str) -> str:
        """``lib:owner:id`` → thêm giọng cộng đồng vào tài khoản (một lần) rồi trả về id thật để đọc."""
        if not voice_id.startswith("lib:"):
            return voice_id
        _l, owner, vid = voice_id.split(":", 2)
        if vid in _ADDED:
            return vid
        r = requests.get(f"{_BASE}/voices/{vid}", headers=self._headers(), timeout=20)
        if r.status_code != 200:
            name = next((v.name for v in (self._voices or []) if v.id == voice_id), vid)
            name = name.replace("🌐 ", "").split(" — ")[0][:40] or vid
            r = requests.post(f"{_BASE}/voices/add/{owner}/{vid}", headers={**self._headers(), "content-type": "application/json"},
                              json={"new_name": name}, timeout=30)
            if r.status_code not in (200, 201) and "already" not in r.text.lower():
                raise RuntimeError("Không thêm được giọng cộng đồng vào tài khoản ElevenLabs "
                                   f"({r.status_code}: {r.text[:200]}). Tài khoản có thể đã hết chỗ chứa giọng — "
                                   "xoá bớt giọng ở elevenlabs.io → My Voices.")
            logger.info("ElevenLabs: đã thêm giọng cộng đồng %s vào tài khoản", name)
        _ADDED.add(vid)
        return vid

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
        with _ADD_LOCK:
            voice_id = self._ensure_library_voice(voice_id)
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
