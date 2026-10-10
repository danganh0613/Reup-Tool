"""Dịch kiểu VideoLingo (github.com/Huanshere/VideoLingo, giấy phép Apache-2.0 — lời nhắc chép nguyên văn từ
core/prompts.py bản 01f8434, 10/2026):

tóm tắt + thuật ngữ (_4_1_summarize) → lô ≤ 10 câu / ≤ 600 ký tự, ngữ cảnh 3 câu gốc trước + 2 câu gốc sau (_4_2_translate)
→ dịch sát → tự phê bình + dịch thoát (translate_lines) → rút gọn câu đọc không kịp (_8_1 check_len_then_trim).

Edit Studio sửa / thêm: bước rút gọn ước lượng thời gian đọc bằng nhịp đọc thật (quality.expected_duration, tốc độ đọc
+ tốc độ video của cấu hình) và xét MỌI câu (bản gốc chỉ xét câu > 3,5 s, ước lượng tiếng Việt sai → không rút gọn câu
nào); còn dài thì thêm một lượt chặt hơn. Lưu tiến độ từng lô (hết lượt giữa chừng → «Thử lại» dịch tiếp), bộ nhớ dịch,
dịch lại câu còn sót chữ Hán. AI tách câu dài (_3_2_split_meaning) cho bộ nhận diện Qwen3-ASR cũng ở đây."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

from app.core.state import Settings
from app.services.ffmpeg import RenderCancelled
from app.services.srt_utils import Cue, wrap_lines
from app.services.translate_router import (LEAK_REQUEST, Checkpoint, ModelUnavailable, Router, TranslationMemory,
                                           _leaks, _load_json, retranslate)

logger = logging.getLogger(__name__)

CHUNK_CHARS, CHUNK_LINES = 600, 10       # _4_2: split_chunks_by_chars(chunk_size=600, max_i=10)
SUMMARY_CHARS = 8000                     # config summary_length
TOLERANCE = 1.5                          # config tolerance: câu được mượn tối đa 1,5 s khoảng lặng sau nó
MAX_SPLIT_TOKENS = 20                    # config max_split_length
STYLE_TAG = "|videolingo"                # khoá bộ nhớ dịch / tiến độ riêng cho cách dịch này


# ── Lời nhắc (nguyên văn VideoLingo core/prompts.py) ──────────────────────

def summary_prompt(src_lang: str, tgt_lang: str, source_content: str, custom_terms: list[dict]) -> str:
    terms_note = ""
    if custom_terms:
        terms_list = [f"- {t['src']}: {t['tgt']} ({t['note']})" for t in custom_terms]
        terms_note = "\n### Existing Terms\nPlease exclude these terms in your extraction:\n" + "\n".join(terms_list)
    return f"""
## Role
You are a video translation expert and terminology consultant, specializing in {src_lang} comprehension and {tgt_lang} expression optimization.

## Task
For the provided {src_lang} video text:
1. Summarize main topic in two sentences
2. Extract professional terms/names with {tgt_lang} translations (excluding existing terms)
3. Provide brief explanation for each term

{terms_note}

Steps:
1. Topic Summary:
   - Quick scan for general understanding
   - Write two sentences: first for main topic, second for key point
2. Term Extraction:
   - Mark professional terms and names (excluding those listed in Existing Terms)
   - Provide {tgt_lang} translation or keep original
   - Add brief explanation
   - Extract less than 15 terms

## INPUT
<text>
{source_content}
</text>

## Output in only JSON format and no other text
{{
  "theme": "Two-sentence video summary",
  "terms": [
    {{
      "src": "{src_lang} term",
      "tgt": "{tgt_lang} translation or original",
      "note": "Brief explanation"
    }},
    ...
  ]
}}

## Example
{{
  "theme": "本视频介绍人工智能在医疗领域的应用现状。重点展示了AI在医学影像诊断和药物研发中的突破性进展。",
  "terms": [
    {{
      "src": "Machine Learning",
      "tgt": "机器学习",
      "note": "AI的核心技术，通过数据训练实现智能决策"
    }},
    {{
      "src": "CNN",
      "tgt": "CNN",
      "note": "卷积神经网络，用于医学图像识别的深度学习模型"
    }}
  ]
}}

Note: Start you answer with ```json and end with ```, do not add any other text.
""".strip()


def shared_prompt(previous, after, summary, note) -> str:
    return f'''### Context Information
<previous_content>
{previous}
</previous_content>

<subsequent_content>
{after}
</subsequent_content>

### Content Summary
{summary}

### Points to Note
{note}'''


def style_section(style: str) -> str:
    style = (style or "").strip()
    if not style:
        return ""
    return f'''### Translation Style
The user asks for the following style. Follow it in the translation, without changing the meaning of the original:
<translation_style>
{style}
</translation_style>

'''


def faith_prompt(lines: str, sp: str, src_lang: str, tgt_lang: str) -> str:
    json_dict = {f"{i}": {"origin": ln, "direct": f"direct {tgt_lang} translation {i}."}
                 for i, ln in enumerate(lines.split("\n"), 1)}
    json_format = json.dumps(json_dict, indent=2, ensure_ascii=False)
    return f'''
## Role
You are a professional Netflix subtitle translator, fluent in both {src_lang} and {tgt_lang}, as well as their respective cultures.
Your expertise lies in accurately understanding the semantics and structure of the original {src_lang} text and faithfully translating it into {tgt_lang} while preserving the original meaning.

## Task
We have a segment of original {src_lang} subtitles that need to be directly translated into {tgt_lang}. These subtitles come from a specific context and may contain specific themes and terminology.

1. Translate the original {src_lang} subtitles into {tgt_lang} line by line
2. Ensure the translation is faithful to the original, accurately conveying the original meaning
3. Consider the context and professional terminology

{sp}

<translation_principles>
1. Faithful to the original: Accurately convey the content and meaning of the original text, without arbitrarily changing, adding, or omitting content.
2. Accurate terminology: Use professional terms correctly and maintain consistency in terminology.
3. Understand the context: Fully comprehend and reflect the background and contextual relationships of the text.
</translation_principles>

## INPUT
<subtitles>
{lines}
</subtitles>

## Output in only JSON format and no other text
```json
{json_format}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''.strip()


def express_prompt(faith: dict, lines: str, sp: str, src_lang: str, tgt_lang: str, style: str) -> str:
    json_format = json.dumps({k: {"origin": v["origin"], "direct": v["direct"],
                                  "reflect": "your reflection on direct translation", "free": "your free translation"}
                              for k, v in faith.items()}, indent=2, ensure_ascii=False)
    return f'''
## Role
You are a professional Netflix subtitle translator and language consultant.
Your expertise lies not only in accurately understanding the original {src_lang} but also in optimizing the {tgt_lang} translation to better suit the target language's expression habits and cultural background.

## Task
We already have a direct translation version of the original {src_lang} subtitles.
Your task is to reflect on and improve these direct translations to create more natural and fluent {tgt_lang} subtitles.

1. Analyze the direct translation results line by line, pointing out existing issues
2. Provide detailed modification suggestions
3. Perform free translation based on your analysis
4. Do not add comments or explanations in the translation, as the subtitles are for the audience to read
5. Do not leave empty lines in the free translation, as the subtitles are for the audience to read

{sp}

{style_section(style)}<Translation Analysis Steps>
Please use a two-step thinking process to handle the text line by line:

1. Direct Translation Reflection:
   - Evaluate language fluency
   - Check if the language style is consistent with the original text
   - Check the conciseness of the subtitles, point out where the translation is too wordy

2. {tgt_lang} Free Translation:
   - Aim for contextual smoothness and naturalness, conforming to {tgt_lang} expression habits
   - Ensure it's easy for {tgt_lang} audience to understand and accept
   - Adapt the language style to match the theme (e.g., use casual language for tutorials, professional terminology for technical content, formal language for documentaries)
</Translation Analysis Steps>

## INPUT
<subtitles>
{lines}
</subtitles>

## Output in only JSON format and no other text
```json
{json_format}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''.strip()


def trim_prompt(text: str, duration: float) -> str:
    rule = '''Consider a. Reducing filler words without modifying meaningful content. b. Omitting unnecessary modifiers or pronouns, for example:
    - "Please explain your thought process" can be shortened to "Please explain thought process"
    - "We need to carefully analyze this complex problem" can be shortened to "We need to analyze this problem"
    - "Let's discuss the various different perspectives on this topic" can be shortened to "Let's discuss different perspectives on this topic"
    - "Can you describe in detail your experience from yesterday" can be shortened to "Can you describe yesterday's experience" '''
    return f'''
## Role
You are a professional subtitle editor, editing and optimizing lengthy subtitles that exceed voiceover time before handing them to voice actors.
Your expertise lies in cleverly shortening subtitles slightly while ensuring the original meaning and structure remain unchanged.

## INPUT
<subtitles>
Subtitle: "{text}"
Duration: {duration} seconds
</subtitles>

## Processing Rules
{rule}

## Processing Steps
Please follow these steps and provide the results in the JSON output:
1. Analysis: Briefly analyze the subtitle's structure, key information, and filler words that can be omitted.
2. Trimming: Based on the rules and analysis, optimize the subtitle by making it more concise according to the processing rules.

## Output in only JSON format and no other text
```json
{{
    "analysis": "Brief analysis of the subtitle, including structure, key information, and potential processing locations",
    "result": "Optimized and shortened subtitle in the original subtitle language"
}}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''.strip()


def split_prompt(sentence: str, num_parts: int = 2, word_limit: int = 20, language: str = "zh") -> str:
    return f"""
## Role
You are a professional Netflix subtitle splitter in **{language}**.

## Task
Split the given subtitle text into **{num_parts}** parts, each less than **{word_limit}** words.

1. Maintain sentence meaning coherence according to Netflix subtitle standards
2. MOST IMPORTANT: Keep parts roughly equal in length (minimum 3 words each)
3. Split at natural points like punctuation marks or conjunctions
4. If provided text is repeated words, simply split at the middle of the repeated words.

## Steps
1. Analyze the sentence structure, complexity, and key splitting challenges
2. Generate two alternative splitting approaches with [br] tags at split positions
3. Compare both approaches highlighting their strengths and weaknesses
4. Choose the best splitting approach

## Given Text
<split_this_sentence>
{sentence}
</split_this_sentence>

## Output in only JSON format and no other text
```json
{{
    "analysis": "Brief description of sentence structure, complexity, and key splitting challenges",
    "split1": "First splitting approach with [br] tags at split positions",
    "split2": "Alternative splitting approach with [br] tags at split positions",
    "assess": "Comparison of both approaches highlighting their strengths and weaknesses",
    "choice": "1 or 2"
}}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
""".strip()


# ── Gọi AI (ask_gpt: JSON, kiểm tra kết quả, thử lại có thêm khoảng trắng như bản gốc) ──

def _ask(router: Router, prompt: str, valid: Callable[[dict], bool] | None = None, tries: int = 3) -> dict:
    last = ""
    for n in range(tries):
        try:
            data = _load_json(router.call(prompt + " " * n))
        except (ValueError, json.JSONDecodeError) as exc:
            last = f"JSON hỏng: {exc}"
            continue
        if isinstance(data, dict) and (valid is None or valid(data)):
            return data
        last = "kết quả thiếu dòng / sai dạng"
    raise ValueError(last)


def source_code(texts: list[str], hint: str = "") -> str:
    """Mã ngôn ngữ nguồn như get_source_language() của VideoLingo (zh / ja / ko …)."""
    joined = "".join(texts)
    if re.search(r"[぀-ヿ]", joined):
        return "ja"
    if re.search(r"[가-힯]", joined):
        return "ko"
    if re.search(r"[一-鿿]", joined):
        return "zh"
    return (hint or "auto").strip() or "auto"


def _custom_terms(glossary: str) -> list[dict]:
    out = []
    for ln in (glossary or "").splitlines():
        if "=" in ln:
            a, b = ln.split("=", 1)
            if a.strip() and b.strip():
                out.append({"src": a.strip(), "tgt": b.strip(), "note": "user glossary"})
    return out[:200]


def _chunks(lines: list[str]) -> list[list[int]]:
    """split_chunks_by_chars: lô ≤ 600 ký tự (tính cả xuống dòng) và ≤ 10 câu."""
    out, cur, size = [], [], 0
    for i, s in enumerate(lines):
        if cur and (size + len(s + "\n") > CHUNK_CHARS or len(cur) == CHUNK_LINES):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(s + "\n")
    if cur:
        out.append(cur)
    return out


def _note(terms: list[dict], chunk: str):
    """search_things_to_note_in_prompt: thuật ngữ có trong lô (None nếu không có — bản gốc in «None»)."""
    hit = [t["src"] for t in terms if str(t.get("src", "")) and str(t["src"]).lower() in chunk.lower()]
    if not hit:
        return None
    return "\n".join(f'{i + 1}. "{t["src"]}": "{t["tgt"]}", meaning: {t.get("note", "")}'
                     for i, t in enumerate(terms) if t["src"] in hit)


# ── Dịch ─────────────────────────────────────────────────────────────────

def translate_cues_vl(cues: list[Cue], *, settings: Settings, target: str, source_hint: str = "", style: str = "",
                      srt_path: str | Path | None = None, fit: dict | None = None,
                      on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
                      cancel: threading.Event | None = None) -> list[Cue]:
    """``fit``: {"tts_speed", "video_speed", "min_gap"} khi có lồng tiếng → rút gọn câu đọc không kịp (None = bỏ qua)."""
    router = Router(settings, log)
    lines = [c.plain.replace("\n", " ").strip() or "…" for c in cues]
    total = len(lines)
    src_lang = source_code(lines, source_hint)
    key_style = (style or "") + STYLE_TAG
    memory = TranslationMemory()
    ckpt = Checkpoint(srt_path, target, key_style)
    results: dict[int, str] = {}
    for i, s in enumerate(lines):
        if str(i) in ckpt.done:
            results[i] = ckpt.done[str(i)]
        elif (hit := memory.get(target, s, key_style)):
            results[i] = hit
    if results and log:
        log(f"Dùng lại {len(results)}/{total} câu từ bộ nhớ dịch / tiến độ trước.")

    def stopped() -> bool:
        return cancel is not None and cancel.is_set()

    # 1) tóm tắt + thuật ngữ (một lượt; lưu cùng tiến độ để dịch tiếp không phải gọi lại)
    custom = _custom_terms(settings.glossary)
    if len(results) < total:
        if "theme" not in ckpt.extra:
            if log:
                log("Dịch: tóm tắt nội dung + lấy thuật ngữ…")
            summ = _ask(router, summary_prompt(src_lang, target, " ".join(lines)[:SUMMARY_CHARS], custom),
                        lambda d: isinstance(d.get("terms"), list) and all(
                            isinstance(t, dict) and {"src", "tgt", "note"} <= set(t) for t in d["terms"]))
            ckpt.extra = {"theme": str(summ.get("theme", "")), "terms": summ["terms"] + custom}
            ckpt.save(total)
            if log:
                log(f"Dịch: {len(summ['terms'])} thuật ngữ — "
                    + ", ".join(f"{t['src']}={t['tgt']}" for t in summ["terms"][:8]))
    theme, terms = ckpt.extra.get("theme", ""), ckpt.extra.get("terms", custom)

    # 2) dịch từng lô: dịch sát → tự phê bình + dịch thoát
    chunks = _chunks(lines)
    for ci, idx in enumerate(chunks):
        if all(i in results for i in idx):
            continue
        if stopped():
            ckpt.save(total)
            memory.save()
            raise RenderCancelled("Đã huỷ dịch — tiến độ đã lưu, mở lại để tiếp tục.")
        chunk = "\n".join(lines[i] for i in idx)
        prev = None if ci == 0 else [lines[i] for i in chunks[ci - 1]][-3:]
        after = None if ci == len(chunks) - 1 else [lines[i] for i in chunks[ci + 1]][:2]
        sp = shared_prompt(prev, after, theme, _note(terms, chunk))
        keys = [str(n) for n in range(1, len(idx) + 1)]

        def has(field: str) -> Callable[[dict], bool]:
            return lambda d: all(isinstance(d.get(k), dict) and str(d[k].get(field, "")).strip() for k in keys)

        try:
            faith = _ask(router, faith_prompt(chunk, sp, src_lang, target), has("direct"))
        except ModelUnavailable:
            ckpt.save(total)
            memory.save()
            raise
        faith = {k: {"origin": lines[idx[n]], "direct": str(faith[k]["direct"]).replace("\n", " ").strip()}
                 for n, k in enumerate(keys)}
        try:
            expr = _ask(router, express_prompt(faith, chunk, sp, src_lang, target, style), has("free"))
            out = [str(expr[k]["free"]).replace("\n", " ").strip() for k in keys]
        except ModelUnavailable:
            ckpt.save(total)
            memory.save()
            raise
        except ValueError as exc:                       # như bản gốc: bước dịch thoát lỗi → giữ bản dịch sát
            logger.warning("VideoLingo lô %d: dịch thoát lỗi (%s) → dùng bản dịch sát", ci + 1, exc)
            out = [faith[k]["direct"] for k in keys]
        for i, t in zip(idx, out):
            results[i] = t
            ckpt.done[str(i)] = t
            memory.put(target, lines[i], t, key_style)
        ckpt.save(total)
        if on_progress:
            on_progress(len(results) / max(1, total))
        if log:
            log(f"Dịch: xong lô {ci + 1}/{len(chunks)} ({router.current.provider}/{router.current.model})")

    texts = [results.get(i, lines[i]) for i in range(total)]
    # 3) câu còn sót chữ Hán / Kana / Hangul → dịch lại riêng (của Edit Studio)
    leak = [i for i in range(total) if _leaks(texts[i], target)]
    if leak and not stopped():
        try:
            new = retranslate(cues, texts, leak, settings=settings, target=target, source_hint=source_hint,
                              style=style, instruction=LEAK_REQUEST, log=log, cancel=cancel)
            for i, t in new.items():
                if t and not _leaks(t, target):
                    texts[i] = t
        except ModelUnavailable:
            raise
        except Exception as exc:                        # noqa: BLE001 — phụ
            logger.warning("Không dịch lại được câu sót chữ gốc: %s", exc)
    # 4) khớp thời lượng lồng tiếng
    if fit and not stopped():
        texts = fit_texts(cues, texts, router, fit, log)
    memory.save()
    ckpt.finish()
    return [Cue(c.index, c.start, c.end, wrap_lines(t), dict(c.meta)) for c, t in zip(cues, texts)]


def fit_texts(cues: list[Cue], texts: list[str], router: Router, fit: dict, log: Callable[[str], None] | None = None,
              ) -> list[str]:
    """check_len_then_trim của VideoLingo với ước lượng đúng nhịp đọc: thời gian có = khung câu + mượn ≤ 1,5 s khoảng
    lặng sau (trên dòng thời gian video ra, trừ khoảng nghỉ tối thiểu giữa hai câu). Ước lượng đọc lâu hơn → AI rút gọn
    (lời nhắc gốc, cho biết số giây); vẫn dài hơn 10 % → thêm một lượt chặt hơn (cho biết số tiếng tối đa)."""
    from app.services.tts.quality import SEC_PER_SYL, expected_duration, syllable_count

    vs = max(0.5, float(fit.get("video_speed", 1.0)))
    ts = max(0.5, float(fit.get("tts_speed", 1.0)))
    gap = max(0.0, float(fit.get("min_gap", 0.0)))
    out, trimmed = list(texts), 0
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else c.end + TOLERANCE
        avail = (c.end - c.start) / vs + min(TOLERANCE, max(0.0, nxt - c.end) / vs) - (gap if i + 1 < len(cues) else 0)
        avail = max(0.3, avail)
        text = out[i]
        if expected_duration(text, ts) <= avail:
            continue
        try:
            new = str(_ask(router, trim_prompt(text, round(avail, 2)), lambda d: bool(str(d.get("result", "")).strip()))
                      ["result"]).strip()
            if expected_duration(new, ts) > avail * 1.1:
                syl = max(3, int(avail * ts / SEC_PER_SYL))
                new2 = str(_ask(router, trim_prompt(new, round(avail, 2)) + f"\n\nThe result must be at most {syl} "
                                "syllables (words) long, keep the key meaning.",
                                lambda d: bool(str(d.get("result", "")).strip()))["result"]).strip()
                if syllable_count(new2) < syllable_count(new):
                    new = new2
        except ModelUnavailable:
            raise
        except ValueError as exc:
            logger.warning("Không rút gọn được câu %d: %s", i + 1, exc)
            continue
        if new and syllable_count(new) < syllable_count(text):
            out[i] = new
            trimmed += 1
    if log:
        log(f"Dịch: khớp thời lượng lồng tiếng — rút gọn {trimmed} câu đọc không kịp")
    return out


# ── AI tách câu dài (_3_2_split_meaning) — cho bộ nhận diện Qwen3-ASR ─────────

_BR = re.compile(r'\[\s*/?\s*br\s*/?\s*\]|<\s*/?\s*br\s*/?\s*>', re.I)
_BREAK_MARKS = ',;:，、；：。！？!?'


def _pick_split(d: dict) -> str | None:
    m = re.search(r"[12]", str(d.get("choice", "")))
    order = [m.group()] if m else []
    for num in order + [n for n in ("1", "2") if n not in order]:
        sp = _BR.sub("[br]", str(d.get(f"split{num}", "")))
        parts = sp.split("[br]")
        if len(parts) > 1 and all(p.strip() for p in parts):
            return sp
    return None


def _split_positions(original: str, modified: str, joiner: str) -> list[int]:
    pos, parts, start = [], modified.split("[br]"), 0
    for i in range(len(parts) - 1):
        best, best_j = 0.0, None
        target = joiner.join(parts[i].split())
        for j in range(start, len(original)):
            sim = SequenceMatcher(None, original[start:j], target).ratio()
            if sim > best:
                best, best_j = sim, j
        if best_j is not None:
            pos.append(best_j)
            start = best_j
    return pos


def _mechanical_split(sentence: str, num_parts: int) -> list[str]:
    lines, rest = [], sentence.strip()
    for left in range(num_parts, 1, -1):
        target, window = len(rest) // left, max(1, len(rest) // (left * 3))
        near = range(max(1, target - window), min(len(rest) - 1, target + window) + 1)
        cuts = [i for i in near if rest[i - 1] in _BREAK_MARKS] or [i for i in near if rest[i].isspace()]
        cut = min(cuts, key=lambda i: abs(i - target)) if cuts else target
        lines.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [ln for ln in lines + [rest] if ln]


def split_long_sentences(sentences: list[str], token_counts: list[int], *, settings: Settings, language: str = "zh",
                         log: Callable[[str], None] | None = None) -> list[str]:
    """Câu > 20 token (spaCy) → AI tách thành ⌈token/20⌉ phần (chỉ lấy VỊ TRÍ cắt, giữ nguyên chữ gốc). Không gọi
    được AI (hết lượt…) → cắt máy ở dấu câu gần giữa nhất như bản gốc."""
    joiner = "" if language in ("zh", "ja", "yue") else " "
    out: list[str] = []
    router = None
    for s, n in zip(sentences, token_counts):
        if n <= MAX_SPLIT_TOKENS:
            out.append(s)
            continue
        parts_n = math.ceil(n / MAX_SPLIT_TOKENS)
        try:
            router = router or Router(settings, log)
            d = _ask(router, split_prompt(s, parts_n, MAX_SPLIT_TOKENS, language), lambda d: _pick_split(d) is not None)
            pts = _split_positions(s, _pick_split(d), joiner)
            pieces, last = [], 0
            for p in pts:
                pieces.append(s[last:p].strip())
                last = p
            pieces.append(s[last:].strip())
            pieces = [x for x in pieces if x]
            out.extend(pieces if len(pieces) > 1 else _mechanical_split(s, parts_n))
        except Exception as exc:                        # noqa: BLE001 — một câu tách hỏng không được dừng cả video
            logger.warning("AI tách câu dài lỗi (%s) → cắt ở dấu câu", exc)
            out.extend(_mechanical_split(s, parts_n))
    return out
