"""Dịch phụ đề đa nhà cung cấp (Gemini · Groq · DeepSeek · OpenAI) với xoay vòng API key,
từ điển thuật ngữ, bộ nhớ dịch và checkpoint để tiếp tục khi gián đoạn.

Thứ tự thử: nhà cung cấp chính (từng key) → các nhà cung cấp khác có key. Lỗi quota/auth
(401/402/403/429) chuyển sang key/nhà cung cấp kế; lỗi khác thử lại vài lần.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from app.config.paths import CACHE_DIR
from app.core.state import Settings
from app.services.ffmpeg import RenderCancelled
from app.services.srt_utils import Cue, wrap_lines

logger = logging.getLogger(__name__)

BATCH = 40
DEFAULT_MODELS = {"Gemini": "gemini-flash-latest", "Groq": "llama-3.3-70b-versatile", "DeepSeek": "deepseek-chat",
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
_DEFAULT_CPS = 16.0


class QuotaError(RuntimeError):
    pass


@dataclass
class Candidate:
    provider: str
    key: str
    model: str


def _keys(text: str) -> list[str]:
    return [k.strip() for k in (text or "").replace(",", "\n").splitlines() if k.strip()]


#: model dự phòng cùng nhà cung cấp — dùng khi model chính quá tải (503) hoặc hết quota riêng của model đó
ALT_MODELS = {"Gemini": ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"],
              "Groq": ["llama-3.1-8b-instant"], "DeepSeek": [], "OpenAI": ["gpt-4o-mini"]}


def build_candidates(settings: Settings) -> list[Candidate]:
    """Danh sách (provider, key, model) theo thứ tự ưu tiên: mọi key của model chính → model dự phòng → nhà cung cấp khác."""
    order = [settings.translate_provider] + [p for p in ("Gemini", "Groq", "DeepSeek", "OpenAI")
                                             if p != settings.translate_provider]
    out: list[Candidate] = []
    for prov in order:
        model = settings.translate_model if prov == settings.translate_provider and settings.translate_model else DEFAULT_MODELS[prov]
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
    return out


def _call_gemini(c: Candidate, prompt: str, timeout: float = 120) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{c.model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}}
    r = requests.post(url, params={"key": c.key}, json=body, timeout=timeout)
    if r.status_code in (401, 402, 403, 429):
        raise QuotaError(f"Gemini {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini không trả kết quả: {json.dumps(data)[:300]}")
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))


def _call_openai_compat(c: Candidate, prompt: str, timeout: float = 120) -> str:
    url = OPENAI_COMPAT[c.provider]
    body = {"model": c.model, "temperature": 0.3,
            "messages": [{"role": "system", "content": "You are a professional subtitle translator. Reply with JSON only."},
                         {"role": "user", "content": prompt}]}
    if c.provider in ("OpenAI", "DeepSeek", "Groq"):
        body["response_format"] = {"type": "json_object"}
    r = requests.post(url, headers={"Authorization": f"Bearer {c.key}"}, json=body, timeout=timeout)
    if r.status_code in (401, 402, 403, 429):
        raise QuotaError(f"{c.provider} {r.status_code}: {r.text[:200]}")
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

    def call(self, prompt: str) -> str:
        tried = 0
        last = ""
        while tried < len(self.cands):
            c = self.current
            fn = _call_gemini if c.provider == "Gemini" else _call_openai_compat
            for attempt in range(3):
                try:
                    return fn(c, prompt)
                except QuotaError as exc:
                    last = str(exc)
                    if self.log:
                        self.log(f"⚠ {c.provider} hết quota/lỗi key ({str(exc)[:80]}) → xoay sang key/model kế")
                    break
                except requests.RequestException as exc:
                    last = str(exc)
                    time.sleep(2.0 * (attempt + 1))
                    if attempt == 2 and self.log:
                        self.log(f"⚠ {c.provider} lỗi mạng: {exc}")
                except RuntimeError as exc:
                    last = str(exc)
                    busy = " 503" in last or " 500" in last or " 502" in last or " 529" in last
                    if " 404" in last:          # model không còn phục vụ → thử model khác ngay
                        if self.log:
                            self.log(f"⚠ {c.provider}: model {c.model} không còn khả dụng → đổi model")
                        break
                    if attempt == 2:
                        if self.log:
                            self.log(f"⚠ {c.provider} {c.model}: {str(exc)[:110]}")
                        break
                    # máy chủ quá tải → chờ lâu hơn (4 s, 8 s) rồi thử lại; lỗi khác chờ ngắn
                    time.sleep(4.0 * (attempt + 1) if busy else 1.5)
            nxt = (self.idx + 1) % len(self.cands)
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
    def key(target: str, text: str) -> str:
        return hashlib.sha1(f"{target}|{' '.join(text.split()).lower()}".encode("utf-8")).hexdigest()

    def get(self, target: str, text: str) -> str | None:
        return self.data.get(self.key(target, text))

    def put(self, target: str, text: str, translation: str) -> None:
        with self._lock:
            self.data[self.key(target, text)] = translation

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

    def __init__(self, srt_path: str | Path | None, target: str) -> None:
        self.path = Path(str(srt_path) + ".progress.json") if srt_path else None
        self.target = target
        self.done: dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("target") == target:
                    self.done = {str(k): str(v) for k, v in (data.get("done") or {}).items()}
            except Exception:
                self.done = {}

    def save(self, total: int) -> None:
        if not self.path:
            return
        try:
            self.path.write_text(json.dumps({"target": self.target, "total": total, "done": self.done,
                                             "updated": time.time()}, ensure_ascii=False), encoding="utf-8")
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
            budgets: list[int] | None = None) -> str:
    if budgets:
        numbered = "\n".join(f"{i + 1}. [max {b} chars] {t}" for i, (t, b) in enumerate(zip(lines, budgets)))
    else:
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
    src = f" The source language is {source_hint}." if source_hint and source_hint != "Tự nhận diện" else ""
    extra = f"\nStyle notes from the user: {style.strip()}" if style.strip() else ""
    gl = _glossary_lines(glossary)
    gl_block = f"\nGlossary — always use these exact translations:\n{gl}" if gl else ""
    reflow_note = ("\n- Lines are consecutive subtitle cues of one speech; you may rephrase each line so the whole "
                   "reads naturally, but keep one output line per input line.") if reflow else ""
    return (
        f"You are a professional subtitle translator. Translate each numbered line into {target}.{src}\n"
        "Rules:\n"
        "- Keep the SAME number of items and the same order; translate every line, never merge or split.\n"
        "- Natural, concise spoken language suitable for on-screen subtitles and voice-over.\n"
        "- Keep names, numbers and units as-is unless the glossary says otherwise. No explanations, no quotes."
        + ("\n- Each line shows a character budget in [max N chars]: the translation is spoken as voice-over in the same "
           "time slot, so stay within the budget (shorten, drop filler words) and never copy the bracket into the output."
           if budgets else "")
        + f"{reflow_note}{gl_block}\n"
        "- Return ONLY a JSON object: {\"translations\": [\"...\", \"...\"]}"
        f"{extra}\n\nLines:\n{numbered}"
    )


def _parse(text: str, expected: int) -> list[str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        data = json.loads(text[s:e + 1]) if s >= 0 and e > s else None
    arr = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(arr, list):
        raise ValueError("JSON không đúng dạng")
    out = [str(x).strip() for x in arr]
    if len(out) != expected:
        raise ValueError(f"Số dòng trả về {len(out)} ≠ {expected}")
    return out


# ── Dịch ──────────────────────────────────────────────────────────────────

def translate_cues(cues: list[Cue], *, settings: Settings, target: str, source_hint: str = "", style: str = "",
                   reflow: bool = False, srt_path: str | Path | None = None,
                   on_progress: Callable[[float], None] | None = None,
                   log: Callable[[str], None] | None = None,
                   cancel: threading.Event | None = None) -> list[Cue]:
    router = Router(settings, log)
    target_en = _LANG_HINT.get(target, target)
    memory = TranslationMemory()
    ckpt = Checkpoint(srt_path, target)
    glossary = settings.glossary
    total = len(cues)
    results: dict[int, str] = {}

    # 1) bộ nhớ dịch + checkpoint
    for i, c in enumerate(cues):
        if str(i) in ckpt.done:
            results[i] = ckpt.done[str(i)]
            continue
        hit = memory.get(target, c.plain) if c.plain else ""
        if hit:
            results[i] = hit
    if results and log:
        log(f"Dùng lại {len(results)}/{total} cue từ bộ nhớ dịch/checkpoint.")

    pending = [i for i in range(total) if i not in results]
    for start in range(0, len(pending), BATCH):
        if cancel is not None and cancel.is_set():
            ckpt.save(total)
            memory.save()
            raise RenderCancelled("Đã huỷ dịch — tiến độ đã lưu, mở lại để tiếp tục.")
        idxs = pending[start:start + BATCH]
        lines = [cues[i].plain or "…" for i in idxs]
        cps = _CHARS_PER_SEC.get(LANG_CODE.get(target, ""), _DEFAULT_CPS)
        budgets = [max(12, int(round(cues[i].duration * cps * 1.15))) for i in idxs]
        translated: list[str] | None = None
        for attempt in range(3):
            try:
                translated = _parse(router.call(_prompt(lines, target_en, source_hint, style, glossary, reflow, budgets)), len(lines))
                break
            except (ValueError, json.JSONDecodeError) as exc:
                logger.warning("Lô dịch không hợp lệ (%s), thử lại", exc)
        failed: set[int] = set()
        if translated is None:
            translated = []
            for i, ln in zip(idxs, lines):
                try:
                    translated.append(_parse(router.call(_prompt([ln], target_en, source_hint, style, glossary, False)), 1)[0])
                except Exception:
                    translated.append(ln)      # giữ nguyên bản gốc cho cue này
                    failed.add(i)
        for i, t in zip(idxs, translated):
            results[i] = t
            if i in failed:
                # không ghi vào checkpoint / bộ nhớ dịch: lần chạy sau sẽ dịch lại thay vì "nhớ" bản chưa dịch
                continue
            ckpt.done[str(i)] = t
            if cues[i].plain:
                memory.put(target, cues[i].plain, t)
        if failed and log:
            log(f"⚠ {len(failed)} cue không dịch được, giữ nguyên bản gốc (chạy lại để dịch tiếp).")
        ckpt.save(total)
        done_n = min(start + BATCH, len(pending))
        if on_progress:
            on_progress(min(1.0, (len(results)) / max(1, total)))
        if log:
            log(f"Đã dịch {done_n}/{len(pending)} cue mới ({router.current.provider}/{router.current.model})")
    memory.save()
    ckpt.finish()
    out: list[Cue] = []
    for i, c in enumerate(cues):
        t = results.get(i, c.text)
        out.append(Cue(c.index, c.start, c.end, wrap_lines(t) if "\n" not in t else t, dict(c.meta)))
    return out


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
