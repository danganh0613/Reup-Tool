"""Dịch phụ đề đa nhà cung cấp (Gemini · Groq · DeepSeek · OpenAI) với xoay vòng API key,
từ điển thuật ngữ, bộ nhớ dịch và checkpoint để tiếp tục khi gián đoạn.

Thứ tự thử: nhà cung cấp chính (từng key) → các nhà cung cấp khác có key. Lỗi quota/auth
(401/402/403/429) chuyển sang key/nhà cung cấp kế — riêng 429 «thử lại sau ≤ 60 s» (giới hạn token/phút, hay gặp
ở gói miễn phí Groq) thì chờ rồi thử lại đúng model đó; lỗi khác thử lại vài lần.
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
from app.services import groq_models
from app.services.ffmpeg import RenderCancelled
from app.services.srt_utils import Cue, wrap_lines

logger = logging.getLogger(__name__)

BATCH = 40
# Groq gói miễn phí chặn theo token TRẢ RA mỗi phút (qwen3.8-27b: 1000, 10/2026) và từ chối hẳn yêu cầu «đoán» ra
# nhiều hơn mức đó («Request too large») → lô nhỏ hơn + báo trước max_tokens vừa đủ (~50 token mỗi câu)
GROQ_BATCH = 16


def max_out(lines: int) -> int:
    """Số token trả ra tối đa cho một lô ``lines`` câu (JSON id + text)."""
    return 100 + 50 * max(1, lines)
DEFAULT_MODELS = {"Gemini": "gemini-flash-latest", "Groq": groq_models.DEFAULT_TEXT[0], "DeepSeek": "deepseek-chat",
                  "OpenAI": "gpt-4o-mini"}
OPENAI_COMPAT = {"Groq": "https://api.groq.com/openai/v1/chat/completions",
                 "DeepSeek": "https://api.deepseek.com/chat/completions",
                 "OpenAI": "https://api.openai.com/v1/chat/completions"}
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
_OVER_BUDGET = 1.15                # dịch xong vẫn dài hơn ngân sách × hệ số này → nhờ AI rút gọn câu đó
_SHORT_TRIED = "⟂đã-rút-gọn|"      # khoá bộ nhớ dịch: bản dịch này đã nhờ AI rút gọn (không gọi lại)
CONTEXT_LINES = 60                 # số câu gốc trước / sau lô gửi kèm làm ngữ cảnh (video ngắn: gần như cả video)

#: yêu cầu rút gọn câu dịch cho vừa thời lượng lồng tiếng (sau khi dịch, và khi giọng đọc vẫn tràn khung)
SHORTEN_REQUEST = ("Shorten each line so it can be spoken within its voice-over time slot: stay within [max N chars], "
                   "keep the key meaning and the speaker's pronouns, natural spoken style, drop filler words and "
                   "repetitions, add nothing new.")

#: câu dịch còn sót chữ nguồn → yêu cầu dịch lại hết sang ngôn ngữ đích
LEAK_REQUEST = ("The CURRENT translation still contains source-language (Chinese / Japanese / Korean) characters. "
                "Translate the whole line into the target language with no CJK characters at all.")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")      # chữ Hán / Kana / Hangul


def _leaks(text: str, target: str) -> bool:
    """Dịch sang ngôn ngữ không dùng chữ Hán / Kana / Hangul mà bản dịch vẫn còn sót."""
    return LANG_CODE.get(target) not in ("zh", "ja", "ko") and bool(_CJK.search(text or ""))


def _wait(seconds: float) -> None:
    """Chờ có ngẫu nhiên: nhiều lô / nhiều key lỗi cùng lúc không gọi lại đúng cùng một nhịp."""
    time.sleep(seconds * random.uniform(0.6, 1.2))


def budget_for(duration: float, target: str) -> int:
    """Số ký tự tối đa để câu dịch đọc kịp trong ``duration`` giây."""
    return max(10, int(round(duration * _CHARS_PER_SEC.get(LANG_CODE.get(target, ""), _DEFAULT_CPS))))


class QuotaError(RuntimeError):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after          # máy chủ bảo «thử lại sau N giây» (giới hạn theo phút)


def _quota_error(r: requests.Response, name: str) -> QuotaError:
    """401/402/403/429 → QuotaError, kèm số giây chờ nếu máy chủ cho biết (Retry-After / «try again in 7.2s» /
    «retryDelay»: «17s» của Google)."""
    wait = None
    if r.status_code == 429:
        try:
            wait = float(r.headers.get("retry-after") or "")
        except ValueError:
            m = re.search(r'try again in ([\d.]+)s|"retryDelay":\s*"([\d.]+)s"', r.text)
            wait = float(m.group(1) or m.group(2)) if m else None
    return QuotaError(f"{name} {r.status_code}: {r.text[:200]}", wait)


@dataclass
class Candidate:
    provider: str
    key: str
    model: str


def _keys(text: str) -> list[str]:
    return [k.strip() for k in (text or "").replace(",", "\n").splitlines() if k.strip()]


#: model dự phòng cùng nhà cung cấp — dùng khi model chính quá tải (503) hoặc hết quota riêng của model đó
ALT_MODELS = {"Gemini": ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"],
              "Groq": ["qwen/qwen3.8-27b"], "DeepSeek": [], "OpenAI": ["gpt-4o-mini"]}
#: model chỉ dùng khi mọi nhà cung cấp khác cũng hỏng — dịch thử Trung → Việt kém rõ (sai thành ngữ, tên con vật)
LAST_RESORT = {"Groq": ["openai/gpt-oss-120b"]}


def build_candidates(settings: Settings) -> list[Candidate]:
    """Danh sách (provider, key, model) theo thứ tự ưu tiên: mọi key của model chính → model dự phòng → nhà cung cấp khác."""
    order = [settings.translate_provider] + [p for p in ("Gemini", "Groq", "DeepSeek", "OpenAI")
                                             if p != settings.translate_provider]
    out: list[Candidate] = []
    for prov in order:
        model = settings.translate_model if prov == settings.translate_provider and settings.translate_model else DEFAULT_MODELS[prov]
        if prov == "Groq":
            model = groq_models.migrate_model(model)
        if prov == "Gemini":
            keys = settings.gemini_keys()
        elif prov == "Groq":
            keys = _keys(settings.groq_api_key)
        elif prov == "DeepSeek":
            keys = _keys(settings.deepseek_api_key)
        else:
            keys = _keys(settings.openai_api_key)
        for m in [model, *(x for x in ALT_MODELS.get(prov, []) if x != model)]:
            for k in keys:
                out.append(Candidate(prov, k, m))
    for prov, models in LAST_RESORT.items():
        keys = _keys(settings.groq_api_key) if prov == "Groq" else []
        out += [Candidate(prov, k, m) for m in models for k in keys if Candidate(prov, k, m) not in out]
    return out


def _call_gemini(c: Candidate, prompt: str, timeout: float = 120, max_tokens: int | None = None) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{c.model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}}
    r = requests.post(url, params={"key": c.key}, json=body, timeout=timeout)
    if r.status_code in (401, 402, 403, 429):
        raise _quota_error(r, "Gemini")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini không trả kết quả: {json.dumps(data)[:300]}")
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))


def _call_openai_compat(c: Candidate, prompt: str, timeout: float = 120, max_tokens: int | None = None) -> str:
    url = OPENAI_COMPAT[c.provider]
    body = {"model": c.model, "temperature": 0.3,
            "messages": [{"role": "system", "content": "You are a professional subtitle translator. Reply with JSON only."},
                         {"role": "user", "content": prompt}]}
    if c.provider in ("OpenAI", "DeepSeek", "Groq"):
        body["response_format"] = {"type": "json_object"}
    if c.provider == "Groq" and c.model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"       # dịch không cần nghĩ lâu: ra ít token hơn ~3,5× (đỡ chạm giới hạn phút)
    elif c.provider == "Groq" and max_tokens:
        body["max_tokens"] = max_tokens        # model suy luận (gpt-oss) không đặt: phần suy nghĩ cũng tính vào đây
    r = requests.post(url, headers={"Authorization": f"Bearer {c.key}"}, json=body, timeout=timeout)
    if r.status_code in (401, 402, 403, 429):
        raise _quota_error(r, c.provider)
    if r.status_code != 200:
        raise RuntimeError(f"{c.provider} {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"]


class Router:
    def __init__(self, settings: Settings, log: Callable[[str], None] | None = None) -> None:
        self.cands = build_candidates(settings)
        self.idx = 0
        self.log = log
        if not self.cands:
            raise RuntimeError("Chưa có API key dịch nào (Cài đặt → API Dịch Thuật).")

    @property
    def current(self) -> Candidate:
        return self.cands[self.idx]

    def call(self, prompt: str, max_tokens: int | None = None) -> str:
        tried = 0
        last = ""
        waited = 0.0                            # giây đã chờ vì giới hạn mỗi phút — chung cả lượt gọi, mọi key / model
        down: set[tuple[str, str]] = set()      # model đã hỏng trong lượt gọi này
        while tried < len(self.cands):
            c = self.current
            fn = _call_gemini if c.provider == "Gemini" else _call_openai_compat
            attempt = 0
            # quá tải / hết giờ / model bị gỡ / chờ hết ngân sách giới hạn phút: lỗi theo MODEL (key cùng tài khoản dùng
            # chung hạn mức) → đổi key không giúp gì, sang model kế
            model_down = False
            while attempt < 3:
                try:
                    return fn(c, prompt, max_tokens=max_tokens)
                except QuotaError as exc:
                    last = str(exc)
                    if exc.retry_after is not None and exc.retry_after <= 60 and waited < 150:
                        # chạm giới hạn token/phút → chờ cho đầy lại rồi thử lại CHÍNH key/model này: giữ model tốt
                        # thay vì rơi xuống model kém hơn; key khác cùng tài khoản dùng chung hạn mức (đo: 2 key kế
                        # tiếp đều 429 ngay) nên đổi key chỉ tốn thêm lượt gọi bị từ chối
                        if self.log:
                            self.log(f"⏳ {c.provider} {c.model} chạm giới hạn mỗi phút → chờ {exc.retry_after:.0f}s rồi thử lại")
                        pause = exc.retry_after * random.uniform(1.0, 1.2) + 0.5
                        time.sleep(pause)
                        waited += pause
                        continue
                    model_down = exc.retry_after is not None
                    if self.log:
                        self.log(f"⚠ {c.provider} hết quota/lỗi key ({str(exc)[:80]}) → xoay sang key/model kế")
                    break
                except requests.RequestException as exc:
                    last = str(exc)
                    _wait(2.0 * (attempt + 1))
                    if attempt == 2:
                        model_down = True
                        if self.log:
                            self.log(f"⚠ {c.provider} lỗi mạng: {exc}")
                    attempt += 1
                except RuntimeError as exc:
                    last = str(exc)
                    busy = " 503" in last or " 500" in last or " 502" in last or " 529" in last
                    if " 404" in last:          # model không còn phục vụ → thử model khác ngay
                        model_down = True
                        if self.log:
                            self.log(f"⚠ {c.provider}: model {c.model} không còn khả dụng → đổi model")
                        break
                    if attempt == 2:
                        model_down = busy
                        if self.log:
                            self.log(f"⚠ {c.provider} {c.model}: {str(exc)[:110]}")
                        break
                    # máy chủ quá tải → chờ lâu hơn (4 s, 8 s) rồi thử lại; lỗi khác chờ ngắn
                    _wait(4.0 * (attempt + 1) if busy else 1.5)
                    attempt += 1
            nxt = (self.idx + 1) % len(self.cands)
            if model_down:
                down.add((c.provider, c.model))
            # các key khác của model đã hỏng sẽ hỏng y hệt (trước: mỗi key thử thêm 3 lần, chờ 4–8 s mỗi lần) — kể cả
            # khi vòng lại từ đầu danh sách
            while tried + 1 < len(self.cands) and (self.cands[nxt].provider, self.cands[nxt].model) in down:
                nxt = (nxt + 1) % len(self.cands)
                tried += 1
            if (self.cands[nxt].provider, self.cands[nxt].model) in down:
                break                                # chỉ còn model đã hỏng
            if self.log and self.cands[nxt] != c:
                n = self.cands[nxt]
                self.log(f"→ Thử tiếp: {n.provider} · {n.model}")
            self.idx = nxt
            tried += 1
        raise RuntimeError(f"Tất cả API key/model đều thất bại. Lỗi cuối: {last[:200]}")


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
    """Lưu tiến độ dịch cạnh SRT nguồn: <srt>.progress.json — mở lại app vẫn tiếp tục được."""

    def __init__(self, srt_path: str | Path | None, target: str, style: str = "") -> None:
        self.path = Path(str(srt_path) + ".progress.json") if srt_path else None
        self.target = target
        self.style = " ".join(style.split())
        self.done: dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("target") == target and data.get("style", "") == self.style:
                    self.done = {str(k): str(v) for k, v in (data.get("done") or {}).items()}
            except Exception:
                self.done = {}

    def save(self, total: int) -> None:
        if not self.path:
            return
        try:
            self.path.write_text(json.dumps({"target": self.target, "style": self.style, "total": total,
                                             "done": self.done, "updated": time.time()}, ensure_ascii=False),
                                 encoding="utf-8")
        except Exception:
            pass

    def finish(self) -> None:
        if self.path and self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass

    @staticmethod
    def list_pending(folder: Path) -> list[dict]:
        out = []
        for p in folder.glob("*.progress.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({"path": p, "srt": Path(str(p)[: -len(".progress.json")]), "target": data.get("target", ""),
                            "done": len(data.get("done") or {}), "total": int(data.get("total", 0)),
                            "updated": float(data.get("updated", 0))})
            except Exception:
                continue
        out.sort(key=lambda d: d["updated"], reverse=True)
        return out


# ── Prompt ────────────────────────────────────────────────────────────────

def _glossary_lines(glossary: str) -> str:
    lines = []
    for ln in (glossary or "").splitlines():
        if "=" in ln:
            src, dst = ln.split("=", 1)
            if src.strip() and dst.strip():
                lines.append(f"- {src.strip()} → {dst.strip()}")
    return "\n".join(lines[:200])


def _prompt(lines: list[str], target: str, source_hint: str, style: str, glossary: str, reflow: bool,
            budgets: list[int] | None = None, ids: list[int] | None = None, context: list[str] | None = None,
            done: list[tuple[str, str]] | None = None) -> str:
    """``ids``: số thứ tự câu trong video (để AI trả đúng id, kiểm được câu thiếu / lệch dòng). ``context``: các câu
    gốc quanh lô (chỉ để hiểu ngữ cảnh). ``done``: (gốc, bản dịch) ngay trước lô — giữ xưng hô, tên, thuật ngữ."""
    ids = ids or list(range(1, len(lines) + 1))
    if budgets:
        numbered = "\n".join(f"{n}. [max {b} chars] {t}" for n, t, b in zip(ids, lines, budgets))
    else:
        numbered = "\n".join(f"{n}. {t}" for n, t in zip(ids, lines))
    src = f" The source language is {source_hint}." if source_hint and source_hint != "Tự nhận diện" else ""
    extra = f"\nStyle notes from the user: {style.strip()}" if style.strip() else ""
    gl = _glossary_lines(glossary)
    gl_block = f"\nGlossary — always use these exact translations:\n{gl}" if gl else ""
    reflow_note = ("\n- Lines are consecutive subtitle cues of one speech; you may rephrase each line so the whole "
                   "reads naturally, but keep one output line per input line.") if reflow else ""
    ctx = ("\n\nWhole-video transcript around these lines (context only — do NOT output it):\n" + "\n".join(context)
           if context else "")
    prev = ("\n\nAlready translated just before (keep the same pronouns, names and terms):\n"
            + "\n".join(f"- {a} → {b}" for a, b in done) if done else "")
    return (
        f"You are a professional subtitle translator for video dubbing. Translate each numbered line into {target}.{src}\n"
        "Rules:\n"
        "- Translate EVERY id listed under «Lines» exactly once, in order; never merge, split or skip a line.\n"
        "- Natural, concise spoken language a native speaker would say (review / sales tone when the video sells "
        "something), suitable for on-screen subtitles and voice-over.\n"
        "- Keep how the speaker addresses the audience (pronouns / forms of address) and product / brand names "
        "consistent across the whole video.\n"
        "- Keep names, numbers and units as-is unless the glossary says otherwise. No explanations, no quotes."
        + ("\n- Each line shows a character budget in [max N chars]: the translation is spoken as voice-over in the same "
           "time slot, so stay within the budget (shorten, drop filler words) and never copy the bracket into the output."
           if budgets else "")
        + f"{reflow_note}{gl_block}\n"
        "- Return ONLY a JSON object: {\"items\": [{\"id\": <id>, \"text\": \"...\"}, ...]} with one item per id."
        f"{extra}{ctx}{prev}\n\nLines:\n{numbered}"
    )


def _load_json(text: str):
    # bỏ phần «<think>…</think>» model suy luận (Qwen3…) đôi khi trả kèm trước JSON
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
    """Gọi AI cho một lô; trả JSON hỏng / thiếu id ``tries`` lần → chia đôi lô, gọi từng nửa (trước: dịch từng câu một,
    lô 40 câu thành 40 lượt). Nửa đầu cũng hỏng hết → model đang trả sai liên tục, thôi chia. Câu thiếu = chưa dịch được."""
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
    Model trả kiểu cũ {"translations": [...]} đúng số câu vẫn nhận."""
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


# ── Dịch ──────────────────────────────────────────────────────────────────

def translate_cues(cues: list[Cue], *, settings: Settings, target: str, source_hint: str = "", style: str = "",
                   reflow: bool = False, srt_path: str | Path | None = None,
                   on_progress: Callable[[float], None] | None = None,
                   log: Callable[[str], None] | None = None,
                   cancel: threading.Event | None = None) -> list[Cue]:
    router = Router(settings, log)
    target_en = _LANG_HINT.get(target, target)
    memory = TranslationMemory()
    ckpt = Checkpoint(srt_path, target, style)
    glossary = settings.glossary
    total = len(cues)
    results: dict[int, str] = {}
    budgets_all = [budget_for(c.duration, target) for c in cues]

    # 1) bộ nhớ dịch + checkpoint
    for i, c in enumerate(cues):
        if str(i) in ckpt.done:
            results[i] = ckpt.done[str(i)]
            continue
        hit = memory.get(target, c.plain, style) if c.plain else ""
        if hit:
            results[i] = hit
    if results and log:
        log(f"Dùng lại {len(results)}/{total} cue từ bộ nhớ dịch/checkpoint.")

    def context_for(a: int, b: int) -> list[str]:
        """Câu gốc quanh lô [a, b] (video ngắn: gần như cả video) — dịch có ngữ cảnh, không rời từng lô."""
        lo, hi = max(0, a - CONTEXT_LINES), min(total, b + 1 + CONTEXT_LINES // 3)
        return [f"{k + 1}. {cues[k].plain}" for k in range(lo, hi) if cues[k].plain and not a <= k <= b]

    def done_before(a: int) -> list[tuple[str, str]]:
        return [(cues[k].plain, results[k]) for k in range(max(0, a - 8), a) if k in results and cues[k].plain]

    pending = [i for i in range(total) if i not in results]
    step = GROQ_BATCH if settings.translate_provider == "Groq" else BATCH
    for start in range(0, len(pending), step):
        if cancel is not None and cancel.is_set():
            ckpt.save(total)
            memory.save()
            raise RenderCancelled("Đã huỷ dịch — tiến độ đã lưu, mở lại để tiếp tục.")
        idxs = pending[start:start + step]

        def call(part: list[int]) -> list[str]:          # lô (hoặc nửa lô) vẫn kèm ngân sách + ngữ cảnh
            ids = [i + 1 for i in part]
            return _parse_items(router.call(_prompt(
                [cues[i].plain or "…" for i in part], target_en, source_hint, style, glossary,
                reflow and len(part) > 1, [budgets_all[i] for i in part], ids, context_for(part[0], part[-1]),
                done_before(part[0])), max_out(len(part))), ids)

        got = _solve(call, idxs)
        failed = {i for i in idxs if not got.get(i)}
        for i in idxs:
            t = got.get(i) or cues[i].plain or "…"        # không dịch được → giữ nguyên bản gốc cho cue này
            results[i] = t
            if i in failed:
                # không ghi vào checkpoint / bộ nhớ dịch: lần chạy sau sẽ dịch lại thay vì "nhớ" bản chưa dịch
                continue
            ckpt.done[str(i)] = t
            if cues[i].plain:
                memory.put(target, cues[i].plain, t, style)
        if failed and log:
            log(f"⚠ {len(failed)} cue không dịch được, giữ nguyên bản gốc (chạy lại để dịch tiếp).")
        ckpt.save(total)
        done_n = min(start + step, len(pending))
        if on_progress:
            on_progress(min(1.0, (len(results)) / max(1, total)))
        if log:
            log(f"Đã dịch {done_n}/{len(pending)} cue mới ({router.current.provider}/{router.current.model})")

    def redo(idxs: list[int], instruction: str, accept: Callable[[int, str], bool]) -> int:
        """Nhờ AI làm lại đúng các câu ``idxs`` (một lượt), giữ bản mới nếu ``accept(i, bản mới)``. Trả số câu đã thay."""
        memory.save()
        try:
            new = retranslate(cues, [results.get(i, "") for i in range(total)], idxs, settings=settings, target=target,
                              source_hint=source_hint, style=style, instruction=instruction, cancel=cancel,
                              budgets={i: budgets_all[i] for i in idxs}, log=log)     # thấy cả lúc chờ / đổi model
        except RenderCancelled:
            raise
        except Exception as exc:
            logger.warning("Không làm lại được bản dịch: %s", exc)
            return 0
        ok = {i: t for i, t in new.items() if t and accept(i, t)}
        for i, t in ok.items():
            results[i] = t
            ckpt.done[str(i)] = t
            memory.put(target, cues[i].plain, t, style)
        return len(ok)

    def stopped() -> bool:
        return cancel is not None and cancel.is_set()

    # 2) câu còn sót chữ gốc (Hán / Kana / Hangul — Qwen hay chêm «俗称»…) → dịch lại riêng các câu đó (một lượt)
    leak = [i for i in range(total) if cues[i].plain and _leaks(results.get(i, ""), target)]
    if leak and not stopped():
        if log:
            log(f"{len(leak)} câu dịch còn sót chữ gốc → AI dịch lại…")
        n = redo(leak, LEAK_REQUEST, lambda i, t: not _leaks(t, target))
        if log:
            log(f"Đã dịch lại {n}/{len(leak)} câu còn sót chữ gốc")

    # 3) câu dịch vẫn dài hơn thời lượng đọc → nhờ AI rút gọn đúng các câu đó (một lượt), giữ bản ngắn hơn.
    # Bản dịch nào đã nhờ rút gọn rồi thì nhớ lại (bộ nhớ dịch) → dịch lại video lấy từ bộ nhớ không gọi rút gọn lần
    # nữa (đo: «rút gọn 0/23» — 2 lượt gọi phí mỗi lần dịch lại)
    def tried_key(i: int) -> str:
        return f"{_SHORT_TRIED}{results[i]}"

    over = [i for i in range(total) if cues[i].plain and results.get(i)
            and len(results[i]) > budgets_all[i] * _OVER_BUDGET and not memory.get(target, tried_key(i), style)]
    if over and not stopped():
        if log:
            log(f"{len(over)} câu dịch dài hơn thời lượng đọc (> {_DEFAULT_CPS:.0f} ký tự/giây) → AI rút gọn…")
        before = {i: tried_key(i) for i in over}
        n = redo(over, SHORTEN_REQUEST,
                 lambda i, t: len(t) < len(results[i]) and not _leaks(t, target) and not cut_off(t, results[i]))
        for k in before.values():
            memory.put(target, k, "1", style)
        if log:
            log(f"Đã rút gọn {n}/{len(over)} câu dài")
    memory.save()
    ckpt.finish()
    out: list[Cue] = []
    for i, c in enumerate(cues):
        t = results.get(i, c.text)
        out.append(Cue(c.index, c.start, c.end, wrap_lines(t) if "\n" not in t else t, dict(c.meta)))
    return out


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
                cancel: threading.Event | None = None, budgets: dict[int, int] | None = None) -> dict[int, str]:
    """Dịch lại các cue ``idxs`` (chỉ số trong ``cues`` — bản gốc) theo yêu cầu người dùng, có xem bản dịch hiện tại
    ``previous`` và câu xung quanh làm ngữ cảnh. Bỏ qua bộ nhớ dịch / checkpoint (đó chính là bản cũ bị chê);
    kết quả mới ghi đè vào bộ nhớ dịch để những lần dịch sau dùng bản mới. ``budgets``: số ký tự tối đa riêng cho
    từng câu (vd rút gọn câu đọc tràn khung). Trả {chỉ số: bản dịch mới}."""
    router = Router(settings, log)
    target_en = _LANG_HINT.get(target, target)
    memory = TranslationMemory()
    budgets = budgets or {}
    idxs = sorted(set(i for i in idxs if 0 <= i < len(cues)))
    out: dict[int, str] = {}
    # cả nhóm câu (kể cả câu rải rác) một lượt, mỗi câu kèm câu gốc trước / sau — trước đây câu cách nhau > 3 câu
    # đi riêng: rút gọn 7 câu tốn 6 lượt
    step = GROQ_BATCH if settings.translate_provider == "Groq" else BATCH
    batches = [idxs[k:k + step] for k in range(0, len(idxs), step)]
    lost = cut = 0

    def call(part: list[int]) -> list[str]:
        inside = set(part)
        items = [(cues[i].plain or "…", (previous[i] if i < len(previous) else "").replace("\n", " "),
                  budgets.get(i) or budget_for(cues[i].duration, target),
                  cues[i - 1].plain if i > 0 and i - 1 not in inside else "",
                  cues[i + 1].plain if i + 1 < len(cues) and i + 1 not in inside else "") for i in part]
        prompt = _retranslate_prompt(items, target_en, source_hint, style, instruction, settings.glossary)
        return _parse_items(router.call(prompt, max_out(len(part))), list(range(1, len(part) + 1)))

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
                    memory.put(target, cues[i].plain, t, style)
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
    router = Router(settings, log)
    target_en = _LANG_HINT.get(target, target)
    edits: dict[int, str] = {}
    notes: list[str] = []
    n = len(current)
    for a in range(0, n, EDIT_CHUNK):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ")
        rows = [(i + 1, (source[i] if source and i < len(source) else "").replace("\n", " "),
                 current[i].replace("\n", " ")) for i in range(a, min(n, a + EDIT_CHUNK))]
        text = router.call(_edit_prompt(rows, instruction, target_en, style, settings.glossary))
        t = text.strip().strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        try:
            data = json.loads(t)
        except json.JSONDecodeError:
            s0, e0 = t.find("{"), t.rfind("}")
            data = json.loads(t[s0:e0 + 1]) if s0 >= 0 and e0 > s0 else {}
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


def detect_language(cues: list[Cue], settings: Settings) -> str:
    sample = " ".join(c.plain for c in cues[:15])[:800]
    if not sample:
        return ""
    try:
        router = Router(settings)
        text = router.call("Identify the language of this text. Return ONLY JSON: {\"language\": \"<English name>\"}\n\n" + sample)
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return str(data.get("language", "")).strip()
    except Exception:
        return ""
