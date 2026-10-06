"""Dịch phụ đề đa nhà cung cấp (Gemini · Groq · DeepSeek · OpenAI) với xoay vòng API key,
từ điển thuật ngữ, bộ nhớ dịch và checkpoint để tiếp tục khi gián đoạn.

Thứ tự thử: nhà cung cấp chính (từng key) → các nhà cung cấp khác có key. Lỗi quota/auth
(401/402/403/429) chuyển sang key/nhà cung cấp kế; lỗi khác thử lại vài lần.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
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
_DEFAULT_CPS = 16.0                # tiếng Việt / Latin: lồng tiếng thoải mái ≤ 15–17 ký tự mỗi giây
_OVER_BUDGET = 1.15                # dịch xong vẫn dài hơn ngân sách × hệ số này → nhờ AI rút gọn câu đó
CONTEXT_LINES = 60                 # số câu gốc trước / sau lô gửi kèm làm ngữ cảnh (video ngắn: gần như cả video)

#: yêu cầu rút gọn câu dịch cho vừa thời lượng lồng tiếng (sau khi dịch, và khi giọng đọc vẫn tràn khung)
SHORTEN_REQUEST = ("Shorten each line so it can be spoken within its voice-over time slot: stay within [max N chars], "
                   "keep the key meaning and the speaker's pronouns, natural spoken style, drop filler words and "
                   "repetitions, add nothing new.")


def _wait(seconds: float) -> None:
    """Chờ có ngẫu nhiên: nhiều lô / nhiều key lỗi cùng lúc không gọi lại đúng cùng một nhịp."""
    time.sleep(seconds * random.uniform(0.6, 1.2))


def budget_for(duration: float, target: str) -> int:
    """Số ký tự tối đa để câu dịch đọc kịp trong ``duration`` giây."""
    return max(10, int(round(duration * _CHARS_PER_SEC.get(LANG_CODE.get(target, ""), _DEFAULT_CPS))))


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
                    _wait(2.0 * (attempt + 1))
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
                    _wait(4.0 * (attempt + 1) if busy else 1.5)
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
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        return json.loads(text[s:e + 1]) if s >= 0 and e > s else None


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
    for start in range(0, len(pending), BATCH):
        if cancel is not None and cancel.is_set():
            ckpt.save(total)
            memory.save()
            raise RenderCancelled("Đã huỷ dịch — tiến độ đã lưu, mở lại để tiếp tục.")
        idxs = pending[start:start + BATCH]
        lines = [cues[i].plain or "…" for i in idxs]
        ids = [i + 1 for i in idxs]
        budgets = [budgets_all[i] for i in idxs]
        ctx, prev = context_for(idxs[0], idxs[-1]), done_before(idxs[0])
        translated: list[str] | None = None
        for _attempt in range(3):
            try:
                translated = _parse_items(router.call(_prompt(lines, target_en, source_hint, style, glossary, reflow,
                                                              budgets, ids, ctx, prev)), ids)
                break
            except (ValueError, json.JSONDecodeError) as exc:
                logger.warning("Lô dịch không hợp lệ (%s), thử lại", exc)
        failed: set[int] = set()
        if translated is None:
            translated = []
            for i, ln in zip(idxs, lines):
                try:            # từng câu vẫn kèm ngân sách + ngữ cảnh
                    translated.append(_parse_items(router.call(_prompt(
                        [ln], target_en, source_hint, style, glossary, False, [budgets_all[i]], [i + 1],
                        context_for(i, i), done_before(i))), [i + 1])[0])
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
                memory.put(target, cues[i].plain, t, style)
        if failed and log:
            log(f"⚠ {len(failed)} cue không dịch được, giữ nguyên bản gốc (chạy lại để dịch tiếp).")
        ckpt.save(total)
        done_n = min(start + BATCH, len(pending))
        if on_progress:
            on_progress(min(1.0, (len(results)) / max(1, total)))
        if log:
            log(f"Đã dịch {done_n}/{len(pending)} cue mới ({router.current.provider}/{router.current.model})")

    # 2) câu dịch vẫn dài hơn thời lượng đọc → nhờ AI rút gọn đúng các câu đó (một lượt), giữ bản ngắn hơn
    over = [i for i in range(total) if cues[i].plain and results.get(i) and len(results[i]) > budgets_all[i] * _OVER_BUDGET]
    if over and not (cancel is not None and cancel.is_set()):
        memory.save()
        if log:
            log(f"{len(over)} câu dịch dài hơn thời lượng đọc (> {_DEFAULT_CPS:.0f} ký tự/giây) → AI rút gọn…")
        try:
            new = retranslate(cues, [results.get(i, "") for i in range(total)], over, settings=settings, target=target,
                              source_hint=source_hint, style=style, instruction=SHORTEN_REQUEST, cancel=cancel,
                              budgets={i: budgets_all[i] for i in over})
        except RenderCancelled:
            raise
        except Exception as exc:
            logger.warning("Không rút gọn được bản dịch: %s", exc)
            new = {}
        shorter = {i: t for i, t in new.items() if t and len(t) < len(results[i])}
        for i, t in shorter.items():
            results[i] = t
            ckpt.done[str(i)] = t
            memory.put(target, cues[i].plain, t, style)
        if log:
            log(f"Đã rút gọn {len(shorter)}/{len(over)} câu dài")
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
CONTEXT = 3        # số câu trước/sau gửi kèm làm ngữ cảnh


def _retranslate_prompt(items: list[tuple[str, str, int]], before: list[str], after: list[str], target: str,
                        source_hint: str, style: str, instruction: str, glossary: str) -> str:
    rows = []
    for k, (src, prev, budget) in enumerate(items):
        rows.append(f"{k + 1}. [max {budget} chars] SOURCE: {src}\n   CURRENT: {prev or '(none)'}")
    src = f" The source language is {source_hint}." if source_hint and source_hint != "Tự nhận diện" else ""
    req = instruction.strip() or "Make it sound more natural and fluent, like real spoken dialogue."
    gl = _glossary_lines(glossary)
    ctx = ""
    if before:
        ctx += "\nLines just BEFORE (context only, do not output):\n" + "\n".join(f"- {t}" for t in before)
    if after:
        ctx += "\nLines just AFTER (context only, do not output):\n" + "\n".join(f"- {t}" for t in after)
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
        + ctx
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
    # gom theo lô các câu liền nhau để ngữ cảnh trước/sau có nghĩa
    batches: list[list[int]] = []
    for i in idxs:
        if batches and i == batches[-1][-1] + 1 and len(batches[-1]) < BATCH:
            batches[-1].append(i)
        elif batches and len(batches[-1]) < 8 and i - batches[-1][-1] <= CONTEXT:
            batches[-1].append(i)            # câu lẻ gần nhau → chung một lô
        else:
            batches.append([i])
    for n, batch in enumerate(batches):
        if cancel is not None and cancel.is_set():
            memory.save()
            raise RenderCancelled("Đã huỷ dịch lại.")
        items = [(cues[i].plain or "…", (previous[i] if i < len(previous) else "").replace("\n", " "),
                  budgets.get(i) or budget_for(cues[i].duration, target)) for i in batch]
        a, b = batch[0], batch[-1]
        before = [cues[j].plain for j in range(max(0, a - CONTEXT), a)]
        after = [cues[j].plain for j in range(b + 1, min(len(cues), b + 1 + CONTEXT))]
        prompt = _retranslate_prompt(items, before, after, target_en, source_hint, style, instruction,
                                     settings.glossary)
        result: list[str] | None = None
        for _attempt in range(3):
            try:
                result = _parse_items(router.call(prompt), list(range(1, len(batch) + 1)))
                break
            except (ValueError, json.JSONDecodeError) as exc:
                logger.warning("Lô dịch lại không hợp lệ (%s), thử lại", exc)
        if result is None:
            raise RuntimeError("AI trả về không đúng định dạng sau 3 lần thử — thử lại hoặc đổi model ở Cài đặt.")
        for i, t in zip(batch, result):
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
    return out


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
