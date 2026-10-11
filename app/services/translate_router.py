"""Gọi Gemini để dịch / sửa phụ đề: xoay vòng API key của cùng một model, từ điển thuật ngữ, bộ nhớ dịch và
checkpoint để tiếp tục khi gián đoạn.

Chỉ dùng model đang chọn — không tự đổi model (đổi giữa chừng làm bản dịch kém hay, lệch giọng văn). 429 «thử lại sau
≤ 60 s» (giới hạn mỗi phút) thì chờ rồi thử lại đúng model đó; hết lượt ngày / quá tải lâu thì dừng, báo người dùng
chọn model khác (``ModelUnavailable``). Cách dịch cả video nằm ở ``translate_vl``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from app.config.paths import CACHE_DIR
from app.core.state import Settings
from app.services.ffmpeg import RenderCancelled
from app.services.srt_utils import Cue

logger = logging.getLogger(__name__)

BATCH = 40                         # số câu mỗi lượt khi dịch lại / rút gọn
DEFAULT_MODEL = "gemini-3.8-flash"
_LANG_HINT = {
    "Tiếng Việt": "Vietnamese", "English": "English", "中文": "Simplified Chinese", "日本語": "Japanese",
    "한국어": "Korean", "Français": "French", "Deutsch": "German", "Español": "Spanish", "ไทย": "Thai",
    "Bahasa Indonesia": "Indonesian", "Русский": "Russian",
}
LANG_CODE = {
    "Tiếng Việt": "vi", "English": "en", "中文": "zh", "日本語": "ja", "한국어": "ko", "Français": "fr", "Deutsch": "de",
    "Español": "es", "ไทย": "th", "Bahasa Indonesia": "id", "Русский": "ru",
}
# tốc độ đọc trung bình (ký tự/giây) để đặt "ngân sách" độ dài mỗi câu — bản dịch vừa thời lượng gốc thì lồng tiếng
# không phải tăng tốc nhiều
_CHARS_PER_SEC = {"zh": 4.5, "ja": 5.0, "ko": 5.5, "th": 12.0}
_DEFAULT_CPS = 16.0                # tiếng Việt / Latin: lồng tiếng thoải mái ≤ 15–17 ký tự mỗi giây

#: yêu cầu rút gọn câu dịch cho vừa thời lượng lồng tiếng (khi giọng đọc vẫn tràn khung)
SHORTEN_REQUEST = ("Shorten each line so it can be spoken within its voice-over time slot: stay within [max N chars], "
                   "keep the key meaning and the speaker's pronouns, natural spoken style, drop filler words and "
                   "repetitions, add nothing new. Never use abbreviations or symbols (write words in full — the line "
                   "is read aloud by TTS), never cut a word or phrase off mid-way.")

#: câu dịch còn sót chữ nguồn → yêu cầu dịch lại hết sang ngôn ngữ đích
LEAK_REQUEST = ("The CURRENT translation still contains source-language (Chinese / Japanese / Korean) characters. "
                "Translate the whole line into the target language with no CJK characters at all.")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")      # chữ Hán / Kana / Hangul


def _leaks(text: str, target: str) -> bool:
    """Dịch sang ngôn ngữ không dùng chữ Hán / Kana / Hangul mà bản dịch vẫn còn sót."""
    return LANG_CODE.get(target) not in ("zh", "ja", "ko") and bool(_CJK.search(text or ""))


def budget_for(duration: float, target: str) -> int:
    """Số ký tự tối đa để câu dịch đọc kịp trong ``duration`` giây."""
    return max(10, int(round(duration * _CHARS_PER_SEC.get(LANG_CODE.get(target, ""), _DEFAULT_CPS))))


#: dấu ở ĐẦU câu lỗi «model hết lượt / quá tải» (câu lỗi của video bị cắt bớt đuôi) — giao diện thấy dấu này thì
#: hiện nút «Chọn model…»
UNAVAILABLE_MARK = "⟦chọn-model⟧"
#: giao diện đặt: gọi (model, câu báo) khi model không dùng được — hiện hộp báo + nút mở chỗ chọn model
on_unavailable: Callable[[str, str], None] | None = None


class ModelUnavailable(RuntimeError):
    """Model đang chọn hết lượt hôm nay / quá tải lâu: dừng, báo người dùng chọn model khác (không tự đổi)."""

    def __init__(self, provider: str, model: str, daily: bool, gone: bool = False) -> None:
        self.model, self.daily = model, daily
        if gone:
            why = f"{provider} {model} không còn dùng được (Google đã gỡ). Chọn model khác ở Cài đặt › AI dùng API key."
        elif daily:
            why = (f"{provider} {model} đã hết lượt hôm nay (gói miễn phí). Chọn model khác ở Cài đặt › AI dùng API key, "
                   "hoặc đợi hạn mức đặt lại (nửa đêm giờ Mỹ ≈ 14–15 giờ chiều).")
        else:
            why = f"{provider} {model} đang quá tải. Đợi vài phút rồi Thử lại, hoặc chọn model khác ở Cài đặt › AI dùng API key."
        self.message = why
        super().__init__(f"{UNAVAILABLE_MARK} {why}")


def notify_unavailable(exc: ModelUnavailable) -> None:
    if on_unavailable is not None:
        try:
            on_unavailable(exc.model, exc.message)
        except Exception:                              # noqa: BLE001 — báo lỗi không được làm hỏng việc đang chạy
            logger.debug("on_unavailable lỗi", exc_info=True)


class QuotaError(RuntimeError):
    def __init__(self, msg: str, retry_after: float | None = None, status: int = 0, per_day: bool = False) -> None:
        super().__init__(msg)
        self.retry_after = retry_after          # máy chủ bảo «thử lại sau N giây» (giới hạn theo phút)
        self.status = status
        self.per_day = per_day                  # hết lượt NGÀY (quotaId …PerDay… — có khi nằm sau 300 ký tự đầu)


def _quota_error(r: requests.Response, name: str) -> QuotaError:
    """401/402/403/429 → QuotaError, kèm số giây chờ nếu máy chủ cho biết (Retry-After / «retryDelay»: «17s»)."""
    wait = None
    if r.status_code == 429:
        try:
            wait = float(r.headers.get("retry-after") or "")
        except ValueError:
            m = re.search(r'try again in ([\d.]+)s|"retryDelay":\s*"([\d.]+)s"', r.text)
            wait = float(m.group(1) or m.group(2)) if m else None
    return QuotaError(f"{name} {r.status_code}: {r.text[:200]}", wait, r.status_code,
                      r.status_code == 429 and "PerDay" in r.text)


@dataclass
class Candidate:
    provider: str
    key: str
    model: str


def build_candidates(settings: Settings) -> list[Candidate]:
    """Mọi key Gemini với đúng model đang chọn."""
    model = settings.translate_model or DEFAULT_MODEL
    return [Candidate("Gemini", k, model) for k in settings.gemini_keys()]


def _call_gemini(c: Candidate, prompt: str, timeout: float = 120) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{c.model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}}
    if c.model.startswith("gemma"):              # Gemma không có chế độ JSON — lời nhắc đã đòi JSON, _load_json tự bóc
        del body["generationConfig"]["responseMimeType"]
    r = requests.post(url, params={"key": c.key}, json=body, timeout=(15, timeout))   # mất mạng → biết sau 15 s
    if r.status_code in (401, 402, 403, 429):
        raise _quota_error(r, "Gemini")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini không trả kết quả: {json.dumps(data)[:300]}")
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))


class Router:
    TRIES = 5                                   # quá tải: chờ 10 → 50 s, 5 lần trước khi dừng báo người dùng

    def __init__(self, settings: Settings, log: Callable[[str], None] | None = None,
                 cancel: threading.Event | None = None) -> None:
        self.cands = build_candidates(settings)
        self.idx = 0
        self.log = log
        self.cancel = cancel
        if not self.cands:
            raise RuntimeError("Chưa có Gemini API key (Cài đặt › AI dùng API key).")

    @property
    def current(self) -> Candidate:
        return self.cands[self.idx]

    def _sleep(self, seconds: float) -> None:
        """Chờ (gọi kèm hệ số ngẫu nhiên: nhiều lô / key lỗi cùng lúc không gọi lại cùng một nhịp); bấm Dừng /
        Bỏ qua là thôi ngay, không treo tới hết lượt chờ quá tải."""
        if self.cancel is None:
            time.sleep(seconds)
        elif self.cancel.wait(seconds):
            raise RenderCancelled("Đã huỷ")

    def call(self, prompt: str) -> str:
        if self.cancel is not None and self.cancel.is_set():
            raise RenderCancelled("Đã huỷ")
        last = ""
        daily = None                            # lần hỏng cuối: True = hết lượt ngày · False = quá tải · None = lỗi khác
        gone = offline = False                  # model bị gỡ (404) · không kết nối được (mạng)
        waited = 0.0                            # giây đã chờ vì giới hạn mỗi phút — chung cả lượt gọi, mọi key
        for tried in range(len(self.cands)):
            c = self.current
            attempt = 0
            stalls = 0                          # số lần liền máy chủ không trả lời (kết nối nửa vời)
            # quá tải / hết giờ / model bị gỡ / hết lượt ngày: lỗi theo MODEL (key cùng tài khoản dùng chung hạn mức)
            # → đổi key không giúp gì, dừng luôn
            model_down = False
            while attempt < self.TRIES:
                try:
                    return _call_gemini(c, prompt)
                except QuotaError as exc:
                    last = str(exc)
                    if exc.status == 429:               # 401/403 = key hỏng → không phải hết lượt
                        daily = exc.per_day or exc.retry_after is None or exc.retry_after > 60
                    if (exc.retry_after is not None and exc.retry_after <= 60 and waited < 150
                            and not exc.per_day):
                        # chạm giới hạn mỗi phút → chờ cho đầy lại rồi thử lại CHÍNH key này (key khác cùng tài khoản
                        # dùng chung hạn mức: đổi key chỉ tốn thêm lượt gọi bị từ chối)
                        if self.log:
                            self.log(f"⏳ {c.provider} {c.model} chạm giới hạn mỗi phút → chờ {exc.retry_after:.0f}s rồi thử lại")
                        pause = exc.retry_after * random.uniform(1.0, 1.2) + 0.5
                        self._sleep(pause)
                        waited += pause
                        continue
                    model_down = exc.retry_after is not None
                    if self.log:
                        self.log(f"⚠ {c.provider} hết lượt / lỗi key ({str(exc)[:80]}) → thử key kế")
                    break
                except requests.RequestException as exc:
                    last = str(exc)
                    stalls = stalls + 1 if isinstance(exc, requests.ReadTimeout) else 0
                    if stalls >= 2:                     # 2 lần liền chờ đủ 2 phút không có trả lời → coi như mất mạng
                        attempt = self.TRIES - 1
                    self._sleep(2.0 * (attempt + 1) * random.uniform(0.6, 1.2))
                    if attempt == self.TRIES - 1:
                        model_down = offline = True
                        if self.log:
                            self.log(f"⚠ {c.provider} lỗi mạng: {str(exc)[:110]}")
                    attempt += 1
                except RuntimeError as exc:
                    last = str(exc)
                    busy = " 503" in last or " 500" in last or " 502" in last or " 529" in last
                    if " 404" in last:          # model không còn phục vụ
                        model_down = gone = True
                        if self.log:
                            self.log(f"⚠ {c.provider}: model {c.model} không còn khả dụng")
                        break
                    if attempt == self.TRIES - 1:
                        model_down = busy
                        daily = False if busy else None
                        if self.log:
                            self.log(f"⚠ {c.provider} {c.model}: {str(exc)[:110]}")
                        break
                    # máy chủ quá tải → chờ lâu dần rồi thử lại (10 → 40 s); lỗi khác chờ ngắn
                    if busy and self.log:
                        self.log(f"⏳ {c.provider} {c.model} đang quá tải → chờ {10 * (attempt + 1)}s rồi thử lại")
                    self._sleep((10.0 * (attempt + 1) if busy else 1.5) * random.uniform(0.6, 1.2))
                    attempt += 1
            if model_down or tried + 1 >= len(self.cands):
                break
            self.idx = (self.idx + 1) % len(self.cands)
        if offline:
            raise RuntimeError("Không kết nối được tới Gemini — kiểm tra mạng rồi Thử lại.")
        if daily is not None or gone:
            exc = ModelUnavailable(self.current.provider, self.current.model, bool(daily), gone)
            if self.log:
                self.log(f"✖ {exc.message}")
            notify_unavailable(exc)
            raise exc
        raise RuntimeError(f"Tất cả API key đều thất bại. Lỗi cuối: {last[:200]}")


# ── Bộ nhớ dịch ───────────────────────────────────────────────────────────

class TranslationMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (CACHE_DIR / "translation_memory.json")
        self._lock = threading.Lock()
        self.data: dict[str, str] = {}
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    @staticmethod
    def key(target: str, text: str, style: str = "") -> str:
        # «v2» + lời dặn trong khoá: đổi lời dặn (vd bỏ câu dẫn bắt buộc) thì không dùng lại bản dịch theo lời dặn cũ
        return hashlib.sha1(f"v2|{target}|{' '.join(style.split())}|{' '.join(text.split()).lower()}"
                            .encode("utf-8")).hexdigest()

    def get(self, target: str, text: str, style: str = "") -> str | None:
        return self.data.get(self.key(target, text, style))

    def put(self, target: str, text: str, translation: str, style: str = "") -> None:
        with self._lock:
            self.data[self.key(target, text, style)] = translation

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if len(self.data) > 50000:
                    for k in list(self.data)[: len(self.data) - 50000]:
                        del self.data[k]
                self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                logger.debug("Không lưu được bộ nhớ dịch", exc_info=True)


# ── Checkpoint ────────────────────────────────────────────────────────────

class Checkpoint:
    """Lưu tiến độ dịch cạnh SRT nguồn: <srt>.progress.json — «Thử lại» / mở lại app thì dịch tiếp phần còn lại."""

    def __init__(self, srt_path: str | Path | None, target: str, style: str = "", source: str = "") -> None:
        """``source``: dấu vân tay các câu nguồn — lời gốc đổi (nhận diện lại, gộp / tách câu) thì bỏ tiến độ cũ."""
        self.path = Path(str(srt_path) + ".progress.json") if srt_path else None
        self.target = target
        self.style = " ".join(style.split())
        self.source = source
        self.done: dict[str, str] = {}
        self.extra: dict = {}                   # chủ đề + thuật ngữ của video (bước tóm tắt)
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (data.get("target") == target and data.get("style", "") == self.style
                        and (not source or data.get("source", source) == source)):
                    self.done = {str(k): str(v) for k, v in (data.get("done") or {}).items()}
                    self.extra = data.get("extra") or {}
            except Exception:
                self.done = {}

    def save(self, total: int) -> None:
        if not self.path:
            return
        try:
            self.path.write_text(json.dumps({"target": self.target, "style": self.style, "source": self.source,
                                             "total": total,
                                             "done": self.done, "extra": self.extra, "updated": time.time()},
                                            ensure_ascii=False),
                                 encoding="utf-8")
        except Exception:
            pass

    def finish(self) -> None:
        if self.path and self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass


# ── Tiện ích lời nhắc / JSON ──────────────────────────────────────────────

def _glossary_lines(glossary: str) -> str:
    lines = []
    for ln in (glossary or "").splitlines():
        if "=" in ln:
            src, dst = ln.split("=", 1)
            if src.strip() and dst.strip():
                lines.append(f"- {src.strip()} → {dst.strip()}")
    return "\n".join(lines[:200])


def _load_json(text: str):
    # bỏ phần «<think>…</think>» model suy luận đôi khi trả kèm trước JSON
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        return json.loads(text[s:e + 1]) if s >= 0 and e > s else None


def _solve(call: Callable[[list[int]], list[str]], idxs: list[int], tries: int = 2) -> dict[int, str]:
    """Gọi AI cho một lô; trả JSON hỏng / thiếu id ``tries`` lần → chia đôi lô, gọi từng nửa. Nửa đầu cũng hỏng hết →
    model đang trả sai liên tục, thôi chia. Câu thiếu = chưa làm được."""
    for _ in range(tries):
        try:
            return dict(zip(idxs, call(idxs)))
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Lô %d câu trả JSON không hợp lệ (%s)", len(idxs), exc)
    if len(idxs) == 1:
        return {}
    mid = len(idxs) // 2
    left = _solve(call, idxs[:mid], 1)
    if not left:
        return {}
    return {**left, **_solve(call, idxs[mid:], 1)}


def _parse(text: str, expected: int) -> list[str]:
    data = _load_json(text)
    arr = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(arr, list):
        raise ValueError("JSON không đúng dạng")
    out = [str(x).strip() for x in arr]
    if len(out) != expected:
        raise ValueError(f"Số dòng trả về {len(out)} ≠ {expected}")
    return out


def _parse_items(text: str, ids: list[int]) -> list[str]:
    """{"items": [{"id", "text"}]} → bản dịch theo đúng thứ tự ``ids``; thiếu / thừa / trùng id → lỗi (thử lại).
    Model trả kiểu {"translations": [...]} đúng số câu vẫn nhận."""
    data = _load_json(text)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return _parse(text, len(ids))
    got: dict[int, str] = {}
    for it in items:
        try:
            got[int(it["id"])] = str(it.get("text", "")).strip()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("JSON không đúng dạng (item thiếu id / text)") from None
    missing = [n for n in ids if n not in got]
    if missing or len(got) != len(ids):
        raise ValueError(f"Thiếu / thừa id: thiếu {missing[:5]}, nhận {len(got)}/{len(ids)}")
    return [got[n] for n in ids]


# ── Dịch lại theo yêu cầu ────────────────────────────────────────────────

RETRANSLATE_PRESETS = ["Tự nhiên, đời thường hơn", "Ngắn gọn hơn (vừa lồng tiếng)", "Xưng hô nhất quán theo ngữ cảnh",
                       "Sát nghĩa bản gốc hơn", "Trẻ trung, bắt trend", "Giọng kể chuyện / thuyết minh phim"]


def _retranslate_prompt(items: list[tuple[str, str, int, str, str]], target: str, source_hint: str, style: str,
                        instruction: str, glossary: str) -> str:
    """``items``: (gốc, bản dịch hiện tại, ngân sách, câu gốc ngay trước, câu gốc ngay sau) — câu kề chỉ để hiểu ngữ
    cảnh (để trống khi câu kề cũng nằm trong lô)."""
    rows = []
    for k, (src, prev, budget, before, after) in enumerate(items):
        around = "; ".join(x for x in (f"before: {before}" if before else "", f"after: {after}" if after else "") if x)
        rows.append(f"{k + 1}. [max {budget} chars] SOURCE: {src}\n   CURRENT: {prev or '(none)'}"
                    + (f"\n   CONTEXT (do not translate): {around}" if around else ""))
    src = f" The source language is {source_hint}." if source_hint and source_hint != "Tự nhận diện" else ""
    req = instruction.strip() or "Make it sound more natural and fluent, like real spoken dialogue."
    gl = _glossary_lines(glossary)
    return (
        f"You are a professional subtitle translator revising subtitles into {target}.{src}\n"
        "The user is NOT satisfied with the CURRENT translations below and wants them redone.\n"
        f"User request (highest priority): {req}\n"
        + (f"General style notes: {style.strip()}\n" if style.strip() else "")
        + "Rules:\n"
        "- Translate from SOURCE again; use CURRENT only to see what the user disliked — do not just copy it.\n"
        "- Keep the SAME number of items and the same order; one output per item, never merge or split.\n"
        "- Spoken as voice-over in the same time slot: stay within [max N chars]; never output the bracket.\n"
        "- Keep names and numbers unless the glossary says otherwise. No explanations, no quotes."
        + (f"\nGlossary — always use these exact translations:\n{gl}" if gl else "")
        + "\n- CONTEXT lines are neighbouring source lines, only to understand the scene — never output them."
        + "\n- Return ONLY a JSON object: {\"items\": [{\"id\": <item number>, \"text\": \"...\"}, ...]} with one item "
        "per numbered item.\n\nItems:\n" + "\n".join(rows)
    )


def retranslate(cues: list[Cue], previous: list[str], idxs: list[int], *, settings: Settings, target: str,
                source_hint: str = "", style: str = "", instruction: str = "",
                on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
                cancel: threading.Event | None = None, budgets: dict[int, int] | None = None,
                memory_key: str | None = None) -> dict[int, str]:
    """``memory_key``: khoá bộ nhớ dịch của bước dịch chính (``translate_vl.memory_style``) — lần «Dịch phụ đề» sau dùng
    lại câu vừa dịch lại thay vì trả về bản AI cũ.

    Dịch (lại) các cue ``idxs`` (chỉ số trong ``cues`` — bản gốc) theo yêu cầu người dùng, có xem bản dịch hiện tại
    ``previous`` (trống = dịch mới) và câu xung quanh làm ngữ cảnh. Bỏ qua bộ nhớ dịch / checkpoint (đó chính là bản cũ
    bị chê); kết quả mới ghi đè vào bộ nhớ dịch. ``budgets``: số ký tự tối đa riêng cho từng câu (vd rút gọn câu đọc
    tràn khung). Trả {chỉ số: bản dịch mới}."""
    router = Router(settings, log, cancel)
    target_en = _LANG_HINT.get(target, target)
    memory = TranslationMemory()
    budgets = budgets or {}
    idxs = sorted(set(i for i in idxs if 0 <= i < len(cues)))
    out: dict[int, str] = {}
    # cả nhóm câu (kể cả câu rải rác) một lượt, mỗi câu kèm câu gốc trước / sau
    batches = [idxs[k:k + BATCH] for k in range(0, len(idxs), BATCH)]
    lost = cut = 0

    def call(part: list[int]) -> list[str]:
        inside = set(part)
        items = [(cues[i].plain or "…", (previous[i] if i < len(previous) else "").replace("\n", " "),
                  budgets.get(i) or budget_for(cues[i].duration, target),
                  cues[i - 1].plain if i > 0 and i - 1 not in inside else "",
                  cues[i + 1].plain if i + 1 < len(cues) and i + 1 not in inside else "") for i in part]
        prompt = _retranslate_prompt(items, target_en, source_hint, style, instruction, settings.glossary)
        return _parse_items(router.call(prompt), list(range(1, len(part) + 1)))

    for n, batch in enumerate(batches):
        if cancel is not None and cancel.is_set():
            memory.save()
            raise RenderCancelled("Đã huỷ dịch lại.")
        result = _solve(call, batch)
        lost += len(batch) - len(result)
        for i, t in result.items():
            if t and i < len(previous) and cut_off(t, previous[i]):
                cut += 1                         # AI chỉ cắt cụt chữ cuối («Hơi hung dữ» → «Hơi hung») → giữ câu cũ
                continue
            if t:
                out[i] = t
                if cues[i].plain:
                    memory.put(target, cues[i].plain, t, memory_key if memory_key is not None else style)
        if on_progress:
            on_progress((n + 1) / len(batches))
        if log:
            log(f"Đã dịch lại {sum(len(x) for x in batches[:n + 1])}/{len(idxs)} câu "
                f"({router.current.provider}/{router.current.model})")
    memory.save()
    if idxs and not out and not cut:
        raise RuntimeError("AI trả về không đúng định dạng — thử lại hoặc đổi model ở Cài đặt.")
    if lost and log:
        log(f"⚠ {lost} câu AI trả sai định dạng — giữ bản dịch cũ")
    if cut and log:
        log(f"Bỏ {cut} bản rút gọn chỉ cắt cụt chữ cuối câu (mất nghĩa) — giữ câu cũ")
    return out


def cut_off(new: str, old: str) -> bool:
    """``new`` chỉ là ``old`` bị cắt bớt chữ cuối («Bắt là phải chịu trách nhiệm» → «… chịu trách»): mất nghĩa, không
    phải rút gọn thật."""
    a, b = re.findall(r"\w+", new.lower()), re.findall(r"\w+", (old or "").lower())
    return 0 < len(a) < len(b) and b[:len(a)] == a


# ── Nhờ AI sửa phụ đề theo yêu cầu (chỉ sửa đúng câu liên quan) ──────────

EDIT_CHUNK = 220          # số câu mỗi lần gửi (phụ đề rất dài → chia lô, mỗi lô thấy cùng yêu cầu)


def _edit_prompt(rows: list[tuple[int, str, str]], instruction: str, target: str, style: str, glossary: str) -> str:
    lines = []
    for n, src, cur in rows:
        lines.append(f"{n}. CURRENT: {cur}" + (f"\n   SOURCE: {src}" if src and src != cur else ""))
    gl = _glossary_lines(glossary)
    return (
        f"You are editing on-screen subtitles in {target} for a video. Each numbered line shows the CURRENT subtitle "
        "(and the original SOURCE line when it is a translation).\n"
        f"The user asks: «{instruction.strip()}»\n"
        "Rules:\n"
        "- Change ONLY the lines this request applies to. Leave every other line exactly as it is and do not list it.\n"
        "- Decide which lines are affected from their meaning (e.g. «the question about taking it home» → every line "
        "with that meaning), not just exact wording.\n"
        "- Return each changed line in full (the whole new subtitle text), keep it short enough to be spoken in the "
        "same time, never merge or split lines, no quotes or explanations inside the text.\n"
        + (f"- General style of this channel: {style.strip()}\n" if style.strip() else "")
        + (f"- Glossary — always use these exact translations:\n{gl}\n" if gl else "")
        + "- Return ONLY JSON: {\"edits\": [{\"n\": <line number>, \"text\": \"<new line>\"}], "
        "\"note\": \"<one short sentence in Vietnamese saying what you changed, or why nothing matched>\"}\n\n"
        "Lines:\n" + "\n".join(lines)
    )


def ai_edit(current: list[str], source: list[str] | None, instruction: str, *, settings: Settings, target: str,
            style: str = "", log: Callable[[str], None] | None = None,
            cancel: threading.Event | None = None) -> tuple[dict[int, str], str]:
    """Sửa phụ đề theo một yêu cầu tự do: AI tự tìm câu liên quan và chỉ trả về những câu đổi.
    Trả ({chỉ số 0-based: câu mới}, ghi chú của AI)."""
    if not instruction.strip():
        return {}, ""
    router = Router(settings, log, cancel)
    target_en = _LANG_HINT.get(target, target)
    edits: dict[int, str] = {}
    notes: list[str] = []
    n = len(current)
    for a in range(0, n, EDIT_CHUNK):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ")
        rows = [(i + 1, (source[i] if source and i < len(source) else "").replace("\n", " "),
                 current[i].replace("\n", " ")) for i in range(a, min(n, a + EDIT_CHUNK))]
        data = _load_json(router.call(_edit_prompt(rows, instruction, target_en, style, settings.glossary))) or {}
        for e in (data.get("edits") or []) if isinstance(data, dict) else []:
            try:
                k = int(e.get("n")) - 1
                new = str(e.get("text", "")).strip()
            except (TypeError, ValueError, AttributeError):
                continue
            if a <= k < min(n, a + EDIT_CHUNK) and new and new != current[k].strip():
                edits[k] = new
        if isinstance(data, dict) and data.get("note"):
            notes.append(str(data["note"]).strip())
    return edits, " ".join(notes)[:300]
