"""Client REST tối giản cho Gemini API: văn bản/JSON, tạo ảnh (Gemini image / Imagen), tạo video (Veo)."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"

# danh sách model: app/services/gemini_models.py (một chỗ duy nhất; có thể cập nhật từ Google)
from app.services.gemini_models import DEFAULT_IMAGE as IMAGE_MODELS  # noqa: E402,F401  (giữ tên cũ cho nơi import)
from app.services.gemini_models import DEFAULT_TEXT as TEXT_MODELS  # noqa: E402,F401
from app.services.gemini_models import DEFAULT_VIDEO as VIDEO_MODELS  # noqa: E402,F401


class GeminiError(RuntimeError):
    pass


class GeminiBusy(GeminiError):
    """Máy chủ Gemini quá tải / lỗi tạm (5xx, mạng) sau nhiều lần thử — nơi gọi nên thử model khác."""


class GeminiModelGone(GeminiError):
    """Model đã bị Google gỡ (404) — nơi gọi nên thử model khác trong ``TEXT_MODELS``."""


class GeminiClient:
    """Gọi Gemini REST. Nhận một key hoặc danh sách key: gặp 429 (hết hạn mức) / 400-403 (key hỏng, bị khoá) thì
    tự chuyển sang key kế tiếp trước khi chờ thử lại."""

    def __init__(self, api_key: str | list[str] | tuple[str, ...]) -> None:
        keys = [api_key] if isinstance(api_key, str) else list(api_key or [])
        self.keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        self._key_idx = 0
        self.api_key = self.keys[0] if self.keys else ""

    def _check(self) -> None:
        if not self.api_key:
            raise GeminiError("Chưa có Gemini API key (vào Cài đặt để nhập).")

    def _rotate_key(self) -> bool:
        """Đổi sang key kế tiếp (vòng tròn). False nếu chỉ có một key."""
        if len(self.keys) < 2:
            return False
        self._key_idx = (self._key_idx + 1) % len(self.keys)
        self.api_key = self.keys[self._key_idx]
        logger.warning("Gemini: chuyển sang API key #%d/%d", self._key_idx + 1, len(self.keys))
        return True

    @staticmethod
    def _gone(r) -> bool:
        """404 + «no longer available» → model bị Google gỡ, phải đổi model (không phải lỗi key)."""
        return r.status_code == 404 and "model" in GeminiClient._api_message(r).lower()

    @staticmethod
    def _bad_key(r) -> bool:
        """400 'API key not valid' / 403 (key bị khoá, chưa bật API) — không phải lỗi nội dung."""
        if r.status_code == 403:
            return True
        if r.status_code == 400:
            msg = GeminiClient._api_message(r).lower()
            return "api key" in msg or "api_key" in msg
        return False

    @staticmethod
    def _api_message(r) -> str:
        try:
            return str(r.json().get("error", {}).get("message", ""))[:300]
        except Exception:
            return r.text[:300]

    def _post(self, path: str, body: dict, *, timeout: float = 120, retries: int = 4) -> dict:
        self._check()
        last: Exception | None = None
        last_status = 0
        last_msg = ""
        model = path.split(":")[0].replace("models/", "")
        for attempt in range(retries):
            # một vòng = thử lần lượt từng key; hết vòng mới chờ rồi thử lại
            for _round in range(max(1, len(self.keys))):
                try:
                    r = requests.post(f"{BASE}/{path}", params={"key": self.api_key}, json=body, timeout=timeout)
                except requests.RequestException as exc:
                    last = exc
                    break
                if r.status_code == 429 or r.status_code >= 500 or self._bad_key(r):
                    last_status, last_msg = r.status_code, self._api_message(r)
                    logger.warning("Gemini %s (%s, key #%d): %s", r.status_code, model, self._key_idx + 1, last_msg[:120])
                    if r.status_code < 500 and self._rotate_key():
                        continue                     # đổi key, thử ngay
                    break
                if self._gone(r):
                    raise GeminiModelGone(f"Model {model} không còn khả dụng: {self._api_message(r)}")
                if r.status_code != 200:
                    raise GeminiError(f"Gemini {r.status_code} ({model}): {self._api_message(r)}")
                return r.json()
            if attempt + 1 < retries:
                wait = min(30.0, 4.0 * 2 ** attempt)          # 4 → 8 → 16 s: 503 «quá tải» thường qua sau vài chục giây
                logger.warning("Gemini (%s) — thử lại sau %.0fs", model, wait)
                time.sleep(wait)
        n = len(self.keys)
        if last_status == 429:
            raise GeminiError(f"Hết hạn mức Gemini cho {model} (429){f' ở cả {n} key' if n > 1 else ''}: "
                              f"{last_msg or 'quota exceeded'} — key không có quota cho model đó (gói miễn phí không có "
                              "tạo ảnh/video) hoặc gọi quá nhanh; đợi rồi thử lại, thêm key khác hoặc bật thanh toán trong Google AI Studio.")
        if last_status in (400, 403):
            raise GeminiError(f"Gemini từ chối API key ({last_status}){f' — đã thử {n} key' if n > 1 else ''}: {last_msg}")
        if last_status >= 500:
            raise GeminiBusy(f"Máy chủ Gemini lỗi {last_status} ({model}): {last_msg} — thử lại sau.")
        raise GeminiBusy(f"Gemini lỗi mạng ({model}): {last}")

    def _get(self, path_or_url: str, *, timeout: float = 60) -> dict:
        self._check()
        url = path_or_url if path_or_url.startswith("http") else f"{BASE}/{path_or_url}"
        r = requests.get(url, params={"key": self.api_key}, timeout=timeout)
        if r.status_code != 200:
            raise GeminiError(f"Gemini {r.status_code}: {r.text[:300]}")
        return r.json()

    # ── Văn bản ───────────────────────────────────────────────────────────
    def generate_text(self, model: str, prompt: str, *, system: str = "", json_mode: bool = True,
                      temperature: float = 0.8, max_tokens: int = 65536) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = self._post(f"models/{model}:generateContent", body, timeout=300)
        cands = data.get("candidates") or []
        if not cands:
            raise GeminiError(f"Gemini không trả kết quả: {json.dumps(data)[:300]}")
        parts = cands[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if cands[0].get("finishReason") == "MAX_TOKENS":
            logger.warning("Gemini cắt vì MAX_TOKENS")
        return text

    def generate_json(self, model: str, prompt: str, *, system: str = "", temperature: float = 0.8,
                      retries: int = 3) -> dict | list:
        last: Exception | None = None
        for _ in range(retries):
            text = self.generate_text(model, prompt, system=system, json_mode=True, temperature=temperature)
            try:
                return _parse_json(text)
            except ValueError as exc:
                last = exc
                logger.warning("JSON không hợp lệ, thử lại: %s", exc)
        raise GeminiError(f"Gemini trả JSON không hợp lệ: {last}")

    # ── Ảnh ───────────────────────────────────────────────────────────────
    def generate_image(self, model: str, prompt: str, out_path: Path, *, aspect_ratio: str = "16:9",
                       reference_image: Path | None = None) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if model.startswith("imagen"):
            body = {"instances": [{"prompt": prompt}],
                    "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio, "personGeneration": "allow_adult"}}
            data = self._post(f"models/{model}:predict", body, timeout=180)
            preds = data.get("predictions") or []
            if not preds or not preds[0].get("bytesBase64Encoded"):
                raise GeminiError(f"Imagen không trả ảnh: {json.dumps(data)[:300]}")
            raw = base64.b64decode(preds[0]["bytesBase64Encoded"])
        else:
            parts: list[dict] = [{"text": prompt}]
            if reference_image and reference_image.exists():
                parts.append({"inlineData": {"mimeType": "image/png",
                                             "data": base64.b64encode(reference_image.read_bytes()).decode()}})
            body = {"contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect_ratio}}}
            data = self._post(f"models/{model}:generateContent", body, timeout=180)
            raw = b""
            for cand in data.get("candidates") or []:
                for p in cand.get("content", {}).get("parts", []):
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and inline.get("data"):
                        raw = base64.b64decode(inline["data"])
                        break
                if raw:
                    break
            if not raw:
                reason = (data.get("candidates") or [{}])[0].get("finishReason", "")
                raise GeminiError(f"Gemini không trả ảnh ({reason or 'không rõ'}); thử đổi prompt hoặc model.")
        out_path.write_bytes(raw)
        return out_path

    # ── Video (Veo) ───────────────────────────────────────────────────────
    def generate_video(self, model: str, prompt: str, out_path: Path, *, aspect_ratio: str = "16:9",
                       duration: int = 8, image: Path | None = None, resolution: str = "720p",
                       cancel: threading.Event | None = None, timeout: float = 900) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        inst: dict = {"prompt": prompt}
        if image and image.exists():
            inst["image"] = {"bytesBase64Encoded": base64.b64encode(image.read_bytes()).decode(),
                             "mimeType": "image/png" if image.suffix.lower() == ".png" else "image/jpeg"}
        allowed = (4, 6, 8) if "veo-3" in model else (5, 6, 7, 8)
        dur = min(allowed, key=lambda d: abs(d - duration))
        params: dict = {"aspectRatio": aspect_ratio, "durationSeconds": dur}
        if "veo-3" in model:
            params["resolution"] = resolution
        body = {"instances": [inst], "parameters": params}
        op = self._post(f"models/{model}:predictLongRunning", body, timeout=120)
        name = op.get("name")
        if not name:
            raise GeminiError(f"Veo không trả operation: {json.dumps(op)[:300]}")
        t0 = time.time()
        while True:
            if cancel is not None and cancel.is_set():
                raise GeminiError("Đã huỷ tạo video")
            if time.time() - t0 > timeout:
                raise GeminiError("Veo quá thời gian chờ")
            time.sleep(8)
            st = self._get(name)
            if st.get("done"):
                if st.get("error"):
                    raise GeminiError(f"Veo lỗi: {st['error']}")
                resp = st.get("response", {})
                samples = (resp.get("generateVideoResponse", {}) or {}).get("generatedSamples") or \
                    resp.get("generatedSamples") or []
                if not samples:
                    raise GeminiError(f"Veo không trả video: {json.dumps(resp)[:300]}")
                uri = (samples[0].get("video") or {}).get("uri", "")
                if not uri:
                    raise GeminiError("Veo không có URI video")
                r = requests.get(uri, headers={"x-goog-api-key": self.api_key}, timeout=300, stream=True)
                if r.status_code != 200:
                    raise GeminiError(f"Tải video Veo lỗi {r.status_code}")
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                return out_path


def _parse_json(text: str) -> dict | list:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # cắt phần thừa trước/sau JSON
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
        s, e = text.find("["), text.rfind("]")
        if s >= 0 and e > s:
            return json.loads(text[s:e + 1])
        raise ValueError("không tìm thấy JSON")
