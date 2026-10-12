"""Dịch kiểu VideoLingo (github.com/Huanshere/VideoLingo, giấy phép Apache-2.0 — lời nhắc chép nguyên văn từ
core/prompts.py bản 01f8434, 10/2026):

tóm tắt + thuật ngữ (_4_1_summarize) → lô ≤ 10 câu / ≤ 600 ký tự, ngữ cảnh 3 câu gốc trước + 2 câu gốc sau (_4_2_translate)
→ dịch sát → tự phê bình + dịch thoát (translate_lines) → rút gọn câu đọc không kịp (_8_1 check_len_then_trim).

Edit Studio sửa / thêm: bước rút gọn ước lượng thời gian đọc bằng nhịp đọc thật (quality.expected_duration, tốc độ đọc
+ tốc độ video của cấu hình) và xét MỌI câu (bản gốc chỉ xét câu > 3,5 s, ước lượng tiếng Việt sai → không rút gọn câu
nào); còn dài thì thêm một lượt chặt hơn. Lưu tiến độ từng lô (hết lượt giữa chừng → «Thử lại» dịch tiếp), bộ nhớ dịch,
dịch lại câu còn sót chữ Hán. AI tách câu dài (_3_2_split_meaning) cho bộ nhận diện Qwen3-ASR cũng ở đây."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

from app.core.state import Settings
from app.services.ffmpeg import RenderCancelled
from app.services.srt_utils import Cue, wrap_lines
from app.services.translate_router import (_LANG_HINT, LEAK_REQUEST, QUOTE_RULE, Checkpoint, ModelUnavailable,
                                           Router, TranslationMemory, _leaks, _load_json, as_text, cut_at_quote,
                                           retranslate)

logger = logging.getLogger(__name__)

CHUNK_CHARS, CHUNK_LINES = 600, 10       # _4_2: split_chunks_by_chars(chunk_size=600, max_i=10)
SUMMARY_CHARS = 8000                     # config summary_length
# dịch cả video một lượt: mỗi lượt ≤ 25 câu / 1500 ký tự — một lượt dài quá thì AI viết ngắn dần về cuối (đo trên video
# mẫu: 39 câu một lượt → nửa sau giọng chỉ lấp 54 % khung, «Con này bắt nuôi được hông?» thành «Nuôi được hông?»)
WHOLE_LINES, WHOLE_CHARS = 25, 1500
MIN_FILL = 0.7                           # câu dịch nên đọc ≥ 70 % thời gian người gốc nói câu đó (ngắn hơn = khoảng lặng)
MIN_MEANING = 0.6                        # câu phóng tác < 60 % số âm tiết bản dịch sát → nhiều khả năng đã bỏ ý → bù lại
TOLERANCE = 1.5                          # config tolerance: câu được mượn tối đa 1,5 s khoảng lặng sau nó
MAX_SPLIT_TOKENS = 20                    # config max_split_length
STYLE_TAG = "|vl-hoso1"                 # khoá bộ nhớ dịch / tiến độ riêng cho cách dịch này (đổi lời nhắc → đổi khoá)


# ── Lời nhắc (nguyên văn VideoLingo core/prompts.py) ──────────────────────

def source_note(src_lang: str) -> str:
    """Lời gốc lấy tự động (nghe giọng nói + đọc chữ trên hình) → có thể sai; AI đoán đúng theo ngữ cảnh rồi mới dịch."""
    return f'''### About the source text
The {src_lang} lines were extracted automatically (speech recognition + on-screen text OCR), so they MAY contain errors:
wrong characters that sound or look alike, missing words, a sentence split across lines, stray on-screen text.
Use the whole context (summary, neighbouring lines, terms) to infer what was really said, then translate that — never
translate an obvious recognition error literally.'''


def transcreation_section(tgt_lang: str) -> str:
    """Dịch thoát cho video ngắn lồng tiếng: người xem nghe hiểu ngay và thấy hấp dẫn (không dịch từng chữ). Giọng văn
    riêng của kênh (xưng hô, tiếng lóng…) do lời dặn của mẫu / người dùng quyết định (``style_section``)."""
    rules = [
        f"This is a short-video voice-over (Douyin / TikTok / Reels). The free translation is a TRANSCREATION for "
        f"{tgt_lang} viewers: it must sound like a native speaker talking naturally, keep the video's mood (funny, "
        "dramatic, cute…) and keep the jokes funny.",
        "Nicknames, puns, memes, slang and culture-specific references: never translate them word for word — use an "
        f"image or expression {tgt_lang} viewers get instantly and find fun.",
        "Natural length: about as long as the original line, filling its speaking time. Drop only filler words — never "
        "information (names, numbers, protection level, consequences…).",
        "Give every line the same care — the last lines as carefully as the first. A line that repeats is translated "
        "in full every time (wording may vary a little), never abbreviated.",
    ]
    return "### Transcreation for viewers\n" + "\n".join(f"{n}. {r}" for n, r in enumerate(rules, 1)) + "\n\n"


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
   - Provide the {tgt_lang} name viewers commonly use (e.g. the common name of an animal), not a literal
     character-by-character rendering; keep the original only when there is no {tgt_lang} name
   - Add brief explanation
   - Extract less than 15 terms

{source_note(src_lang)}

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
{QUOTE_RULE}
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

{source_note(src_lang)}

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
{QUOTE_RULE}
'''.strip()


def express_prompt(faith: dict, lines: str, sp: str, src_lang: str, tgt_lang: str, style: str,
                   budgets: dict[str, tuple[int, int]] | None = None) -> str:
    """``budgets``: số âm tiết (tối thiểu, tối đa) từng câu theo khung lồng tiếng — có thì AI viết vừa khung luôn."""
    budgets = budgets or {}
    json_format = json.dumps({k: {"origin": v["origin"], "direct": v["direct"],
                                  **({"syllables": f"{budgets[k][0]}-{budgets[k][1]}"} if k in budgets else {}),
                                  "reflect": "your reflection on direct translation", "free": "your free translation"}
                              for k, v in faith.items()}, indent=2, ensure_ascii=False)
    fit_rule = ("6. Each free translation must have a number of syllables inside its \"syllables\" range (one syllable = "
                "one spoken word / Vietnamese syllable): long enough to fill the speaking time, short enough to fit\n"
                if budgets else "")
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
{fit_rule}
{sp}

{transcreation_section(tgt_lang)}{style_section(style)}<Translation Analysis Steps>
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
{QUOTE_RULE}
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
{QUOTE_RULE}
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
{QUOTE_RULE}
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


_CODE_NAME = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English", "vi": "Vietnamese"}


def memory_style(style: str, glossary: str, model: str = "") -> str:
    """Khoá «phong cách» của bộ nhớ dịch: lời dặn + cách dịch + từ điển + model dịch (đổi từ điển / đổi sang model
    khác → dịch mới, không lấy lại bản dịch của model cũ)."""
    gloss = " ".join((glossary or "").split())
    return ((style or "") + STYLE_TAG + (f"|g{hashlib.sha1(gloss.encode('utf-8')).hexdigest()[:8]}" if gloss else "")
            + (f"|m{model.strip().lower()}" if model and model.strip() else ""))


def lang_name(value: str) -> str:
    """Tên tiếng Anh cho lời nhắc: «Tiếng Việt» → Vietnamese, «zh» → Chinese, «Tự nhận diện» → the original language."""
    v = (value or "").strip()
    return _LANG_HINT.get(v) or _CODE_NAME.get(v) or ("the original language" if v in ("", "auto", "Tự nhận diện") else v)


def source_name(texts: list[str], hint: str = "") -> str:
    """Ngôn ngữ nguồn cho lời nhắc; video chữ Hán / Kana / Hangul xen nhiều câu tiếng Anh → nói rõ để AI dịch cả hai."""
    code = source_code(texts, hint)
    name = lang_name(code)
    if code in ("zh", "ja", "ko"):
        latin = sum(1 for t in texts if len(re.findall(r"[A-Za-z]{3,}", t)) >= 2)
        if latin >= max(2, len(texts) // 10):
            name += " mixed with some English"
    return name


#: chữ chỉ tiếng Việt có (không tính à / é / ô… mà tiếng Pháp / Tây Ban Nha cũng dùng)
_VI_CHARS = re.compile(r"[ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)


def same_language(texts: list[str], target: str, hint: str = "") -> bool:
    """Lời gốc đã là ngôn ngữ đích (video tiếng Việt mà mẫu bật «Dịch sang Tiếng Việt») → khỏi dịch Việt → Việt tốn
    lượt Gemini. Chỉ nhận chắc: tiếng Việt (chữ riêng của tiếng Việt) và Trung / Nhật / Hàn (bộ chữ); còn lại cứ dịch."""
    from app.services.translate_router import LANG_CODE

    if hint and hint == target:
        return True
    code = LANG_CODE.get(target, "")
    lines = [t for t in texts if t and t.strip()]
    if not lines:
        return False
    if code == "vi":
        vi = sum(1 for t in lines if _VI_CHARS.search(t))
        cjk = sum(1 for t in lines if re.search(r"[぀-ヿ一-鿿가-힯]", t))
        return vi >= 0.5 * len(lines) and cjk <= 0.05 * len(lines)
    if code in ("zh", "ja", "ko"):
        return source_code(lines) == code
    return False


def source_code(texts: list[str], hint: str = "") -> str:
    """Mã ngôn ngữ nguồn (zh / ja / ko …). Đã chọn rõ «Ngôn ngữ video» → dùng luôn; «Tự nhận diện» → theo chữ chiếm
    đa số: tiếng Nhật luôn nhiều kana, tiếng Hàn chủ yếu Hangul — vài chữ lạc không đổi được cả video."""
    from app.services.translate_router import LANG_CODE

    h = (hint or "").strip()
    if h and h not in ("auto", "Tự nhận diện"):
        return LANG_CODE.get(h, h)
    joined = "".join(texts)
    kana = len(re.findall(r"[぀-ヿ]", joined))
    hangul = len(re.findall(r"[가-힯]", joined))
    han = len(re.findall(r"[一-鿿]", joined))
    cjk = kana + hangul + han
    if cjk < 3:
        return h or "auto"
    if hangul >= 0.3 * cjk:
        return "ko"
    if kana >= 0.05 * cjk:
        return "ja"
    return "zh" if han else (h or "auto")


def _custom_terms(glossary: str) -> list[dict]:
    out = []
    for ln in (glossary or "").splitlines():
        if "=" in ln:
            a, b = ln.split("=", 1)
            if a.strip() and b.strip():
                out.append({"src": a.strip(), "tgt": b.strip(), "note": "user glossary"})
    return out[:200]


#: câu tiếng Việt trọn vẹn không kết thúc bằng các hư từ này — gặp là câu bị cụt vế sau («Bắt xong là»); «được»,
#: «mà», «với», «hay» đứng cuối vẫn là câu thường («Nuôi được», «Chờ tao với») nên không tính
_DANGLING = re.compile(r"(?<!\w)(là|của|và|để|rằng|nhưng|hoặc|những|các)[\s,.…!?]*$", re.I)


def _free_broken(free, direct: str) -> bool:
    """Câu dịch thoát không dùng được: thiếu, bị cắt ở dấu " (``cut_at_quote``), hoặc kết thúc lửng bằng hư từ («Bắt
    xong là»). Không so độ dài với bản dịch sát: lời dặn đòi cô đọng, câu ngắn mà đủ ý («Hồng hoàng đấy») vẫn dùng."""
    return cut_at_quote(free) or bool(_DANGLING.search(str(free).strip()))


def whole_prompt(rows: list[str], budgets: list[tuple[int, int]] | None, src_lang: str, tgt_lang: str, style: str,
                 custom_terms: list[dict], before: list[str], known_terms: list[dict] | None = None,
                 brief: str = "", speakers: list[str] | None = None) -> str:
    """Cả video (hoặc một phần dài) trong một lượt: AI đọc hết câu chuyện rồi mới dịch — xưng hô, tên gọi nhất quán,
    hiểu câu đùa / biệt danh theo cả video. Mỗi câu trả cả dịch sát lẫn dịch thoát (vừa số âm tiết của câu)."""
    numbered = "\n".join(f"{n}. " + (f"[{budgets[n - 1][0]}-{budgets[n - 1][1]} syllables] " if budgets else "")
                          + (f"[{speakers[n - 1]}] " if speakers and speakers[n - 1] else "") + t
                          for n, t in enumerate(rows, 1))
    brief_sec = ("## Script brief — decided for the whole video, follow it exactly (who speaks, pronoun pair, names, "
                 "repeated lines word for word)\n" + brief.strip() + "\n\n") if brief.strip() else ""
    fit = (", with a number of syllables inside its [min-max syllables] range (one syllable = one spoken word / "
           "Vietnamese syllable): long enough to fill the speaking time, short enough to fit") if budgets else ""
    terms = ("### Terms the user requires (always use these)\n"
             + "\n".join(f"- {t['src']} → {t['tgt']}" for t in custom_terms) + "\n\n") if custom_terms else ""
    if known_terms:
        terms += ("### Terms already used earlier in this video (keep them the same)\n"
                  + "\n".join(f"- {t['src']} → {t['tgt']}" for t in known_terms) + "\n\n")
    ctx = ("### Context: what was said just before this part, with the translation already used (do not translate "
           "again — keep the same pronouns and tone)\n" + "\n".join(before) + "\n\n" if before else "")
    sample = json.dumps({"terms": [{"src": f"{src_lang} term", "tgt": f"{tgt_lang} rendering"}],
                         "lines": {"1": {"direct": "faithful translation", "free": "final line"},
                                   "2": {"direct": "...", "free": "..."}}}, indent=2, ensure_ascii=False)
    return f'''
## Role
You are an expert subtitle translator and localizer of short videos, fluent in {src_lang} and {tgt_lang} and their
internet culture.

## Task
Below is the whole numbered transcript. First read ALL of it to understand the story, who is talking, the jokes
and the running gags. Then:
1. "terms": the key names / terms (people, animals, places, nicknames, slang) with the {tgt_lang} rendering you will
   use consistently in every line (at most 15; the common name viewers know, not a literal rendering).
2. For EVERY line number give:
   - "direct": a faithful {tgt_lang} translation of what is really meant (fix recognition errors first)
   - "free": the final line viewers hear and read — the transcreation (rules below){fit}
Exactly one output per line number, in the same order; never merge, split or skip lines; never leave "free" empty.

{source_note(src_lang)}

{transcreation_section(tgt_lang)}{style_section(style)}{brief_sec}{terms}{ctx}## Lines ({len(rows)} lines)
{numbered}

## Output in only JSON format and no other text
```json
{sample}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
{QUOTE_RULE}
'''.strip()


# ── Hồ sơ kịch bản: AI đọc cả video MỘT lần, chốt người nói + xưng hô, tên riêng, bản dịch câu lặp — mọi phần dịch sau
#    (và Nhờ AI sửa) theo đúng hồ sơ này → cả video nhất quán. Lưu thành chữ (người dùng xem / sửa được) ──────────
BRIEF_PEOPLE, BRIEF_TERMS, BRIEF_REPEAT = "Người nói:", "Tên riêng / thuật ngữ:", "Câu lặp (dùng đúng bản này ở mọi chỗ):"


def _norm_src(s: str) -> str:
    """Lời gốc rút gọn để so câu lặp (bỏ dấu câu, khoảng trắng)."""
    return re.sub(r"[\s\W_]+", "", s or "").lower()


def repeated_groups(lines: list[str]) -> dict[str, list[int]]:
    """Câu gốc xuất hiện ≥ 2 lần (vd câu hỏi lặp mỗi đoạn review) → {lời gốc rút gọn: [chỉ số câu]}."""
    groups: dict[str, list[int]] = {}
    for i, t in enumerate(lines):
        k = _norm_src(t)
        if len(k) >= 2:
            groups.setdefault(k, []).append(i)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def brief_prompt(lines: list[str], speakers: list[str] | None, src_lang: str, tgt_lang: str, style: str,
                 custom_terms: list[dict], repeats: list[tuple[str, int, tuple[int, int] | None]]) -> str:
    numbered = "\n".join(f"{n}. " + (f"[{speakers[n - 1]}] " if speakers and speakers[n - 1] else "") + t
                         for n, t in enumerate(lines, 1))
    rep_rows = "\n".join(f"- {src} (x{cnt}" + (f", {r[0]}-{r[1]} syllables" if r else "") + ")" for src, cnt, r in repeats)
    terms = ("### Terms the user requires (always use these)\n"
             + "\n".join(f"- {t['src']} → {t['tgt']}" for t in custom_terms) + "\n\n") if custom_terms else ""
    sample = json.dumps({"summary": "…", "tone": "…",
                         "people": [{"speaker": "Người 1", "who": "nam, người hỏi", "self": "tao", "calls": "mày"}],
                         "terms": [{"src": f"{src_lang} name", "tgt": f"{tgt_lang} rendering"}],
                         "repeated": [{"src": "repeated source line", "tgt": f"one {tgt_lang} line for every occurrence"}]},
                        ensure_ascii=False, indent=2)
    return f'''
## Role
You are the lead localizer writing the SCRIPT BRIEF for translating a short video from {src_lang} into {tgt_lang}.
Translators of every part of the video will follow this brief, so it must make the whole video consistent.

## Whole transcript (read ALL of it first; [Speaker · gender] = who is talking, detected from the audio)
{numbered}

## Repeated lines (the same {src_lang} line said several times)
{rep_rows or "(none)"}

## Write the brief — every text value in Vietnamese
1. "summary": what the video is about, 1–2 sentences.
2. "tone": the voice to use (fun / serious, slang level, rhythm).
3. "people": one entry per speaker label (or the narrator if there are no labels): "who" (gender, role in the
   video), "self" = how they call themselves in {tgt_lang}, "calls" = how they address the listener / the other
   speaker. Pick ONE pronoun pair and keep it for the whole video. Questions and their answers must stay about the
   same subject.
4. "terms": names / terms (people, animals, places, nicknames) → the {tgt_lang} rendering viewers know (at most 20).
5. "repeated": for EACH repeated line above, ONE final {tgt_lang} line to use at every occurrence — natural, inside its
   syllable range, consistent with "people".
{style_section(style)}{terms}
## Output only JSON
```json
{sample}
```
{QUOTE_RULE}
'''.strip()


def render_brief(d: dict) -> str:
    """Hồ sơ AI trả (JSON) → chữ để đọc / sửa và để gửi kèm mọi lượt dịch / sửa."""
    out: list[str] = []
    if as_text(d.get("summary")).strip():
        out.append(f"Nội dung: {as_text(d['summary']).strip()}")
    if as_text(d.get("tone")).strip():
        out.append(f"Giọng điệu: {as_text(d['tone']).strip()}")
    people = [x for x in d.get("people") or [] if isinstance(x, dict)]
    if people:
        out.append(BRIEF_PEOPLE)
        for x in people:
            name = as_text(x.get("speaker") or x.get("name")).strip() or "Người kể"
            who = as_text(x.get("who")).strip()
            me, you = as_text(x.get("self")).strip(), as_text(x.get("calls")).strip()
            bits = [b for b in (f"xưng «{me}»" if me else "", f"gọi người nghe «{you}»" if you else "") if b]
            out.append(f"- {name}" + (f" ({who})" if who else "") + (": " + ", ".join(bits) if bits else ""))
    for head, key in ((BRIEF_TERMS, "terms"), (BRIEF_REPEAT, "repeated")):
        rows = [(as_text(t.get("src")).strip(), as_text(t.get("tgt")).strip()) for t in d.get(key) or []
                if isinstance(t, dict)]
        rows = [(a, b) for a, b in rows if a and b]
        if rows:
            out.append(head)
            out += [f"- {a} = {b}" for a, b in rows]
    return "\n".join(out)


def parse_brief(text: str) -> tuple[list[dict], dict[str, str]]:
    """Chữ hồ sơ (có thể người dùng đã sửa) → (thuật ngữ, {lời gốc rút gọn: bản dịch chốt của câu lặp})."""
    terms: list[dict] = []
    reps: dict[str, str] = {}
    sec = ""
    for ln in (text or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        if not t.startswith("-"):
            sec = "terms" if t.startswith(BRIEF_TERMS[:9]) else "rep" if t.startswith(BRIEF_REPEAT[:7]) else ""
            continue
        if sec not in ("terms", "rep") or "=" not in t:
            continue
        a, b = (x.strip(" «»\"'") for x in t[1:].split("=", 1))
        if a and b:
            if sec == "terms":
                terms.append({"src": a, "tgt": b})
            else:
                reps[_norm_src(a)] = b
    return terms, reps


def make_brief(router: Router, cues: list[Cue], lines: list[str], speakers: list[str] | None, fit: dict | None, *,
               src_lang: str, tgt_lang: str, style: str, custom_terms: list[dict],
               log: Callable[[str], None] | None = None) -> str:
    """Một lượt gọi: AI đọc cả kịch bản → hồ sơ (chữ). Lỗi định dạng → "" (vẫn dịch như cũ, không hồ sơ)."""
    repeats = []
    for idx in repeated_groups(lines).values():
        r = None
        if fit:
            rs = [syllable_range(cues, i, fit) for i in idx]
            r = (min(a for a, _b in rs), min(b for _a, b in rs))
        repeats.append((lines[idx[0]], len(idx), r))
    try:
        d = _ask(router, brief_prompt(lines, speakers, src_lang, tgt_lang, style, custom_terms, repeats),
                 lambda d: isinstance(d.get("people", []), list) and isinstance(d.get("repeated", []), list), tries=2)
    except ValueError as exc:
        logger.warning("Không soạn được hồ sơ kịch bản: %s", exc)
        if log:
            log("⚠ Chưa soạn được hồ sơ kịch bản — vẫn dịch như thường.")
        return ""
    text = render_brief(d)
    if log:
        people = sum(1 for x in d.get("people") or [] if isinstance(x, dict))
        log(f"Hồ sơ kịch bản: {people} người nói · {len(d.get('terms') or [])} tên riêng · "
            f"{len(d.get('repeated') or [])} câu lặp chốt một bản dịch")
    return text


def unify_repeats(lines: list[str], texts: list[str], canon: dict[str, str], skip: set[int]) -> list[int]:
    """Câu cùng lời gốc → cùng một bản dịch: bản hồ sơ chốt, không có thì bản dùng nhiều nhất (hoà → câu đầu).
    Trả các câu đã đổi."""
    changed: list[int] = []
    for k, idx in repeated_groups(lines).items():
        idx = [i for i in idx if i not in skip and texts[i].strip()]
        if len(idx) < 2:
            continue
        target = canon.get(k)
        if not target:
            seen = [texts[i].strip() for i in idx]
            target = max(seen, key=lambda t: (seen.count(t), -seen.index(t)))
        for i in idx:
            if texts[i].strip() != target:
                texts[i] = target
                changed.append(i)
    return sorted(changed)


def _windows(idx: list[int], lines: list[str]) -> list[list[int]]:
    """Chia các câu cần dịch thành phần ≤ ``WHOLE_LINES`` câu / ``WHOLE_CHARS`` ký tự (video 2 phút = một phần)."""
    out, cur, size = [], [], 0
    for i in idx:
        if cur and (len(cur) >= WHOLE_LINES or size + len(lines[i]) > WHOLE_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(lines[i])
    if cur:
        out.append(cur)
    return out


def _translate_whole(router: Router, idx: list[int], lines: list[str], budgets: list[tuple[int, int]] | None, *,
                     src_lang: str, tgt_lang: str, style: str, custom_terms: list[dict],
                     known_terms: list[dict] | None = None,
                     done: dict[int, str] | None = None, brief: str = "",
                     speakers: list[str] | None = None) -> tuple[list[dict], dict[int, tuple]]:
    """Một lượt cho cả phần ``idx`` → (thuật ngữ, {câu: (dịch sát, dịch thoát)}). Thiếu câu / dịch sát cụt → ValueError
    (``_ask`` hỏi lại 3 lần; nơi gọi chuyển phần đó sang dịch từng lô)."""
    keys = [str(n) for n in range(1, len(idx) + 1)]
    done = done or {}
    before = [lines[i] + (f"  →  {done[i]}" if i in done else "") for i in range(max(0, idx[0] - 3), idx[0])]

    def ok(d: dict) -> bool:
        got = d.get("lines")
        return isinstance(got, dict) and all(isinstance(got.get(k), dict) and not cut_at_quote(got[k].get("direct"))
                                             for k in keys)

    d = _ask(router, whole_prompt([lines[i] for i in idx], budgets, src_lang, tgt_lang, style, custom_terms, before,
                                  known_terms, brief, [speakers[i] for i in idx] if speakers else None), ok)
    terms = [t for t in d.get("terms") or [] if isinstance(t, dict) and str(t.get("src", "")).strip()
             and str(t.get("tgt", "")).strip()]
    return terms, {i: (as_text(d["lines"][k]["direct"]).replace("\n", " ").strip(), as_text(d["lines"][k].get("free")))
                   for i, k in zip(idx, keys)}


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
                      cancel: threading.Event | None = None, speakers: list[str] | None = None,
                      brief: str | None = None, on_brief: Callable[[str], None] | None = None) -> list[Cue]:
    """``fit``: {"tts_speed", "video_speed", "min_gap"} khi có lồng tiếng → rút gọn câu đọc không kịp (None = bỏ qua).
    ``speakers``: nhãn người nói từng câu («Người 1 · Nam», "" = chưa rõ) — AI biết ai nói để xưng hô đúng.
    ``brief``: hồ sơ kịch bản đã có (người dùng có thể đã sửa); None → AI soạn (1 lượt gọi) rồi ``on_brief(chữ)``."""
    router = Router(settings, log, cancel)
    lines = [c.plain.replace("\n", " ").strip() or "…" for c in cues]
    total = len(lines)
    src_lang = source_name(lines, source_hint)
    tgt_lang = lang_name(target)
    key_style = memory_style(style, settings.glossary, settings.active_model())
    memory = TranslationMemory()
    source = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:12]
    ckpt = Checkpoint(srt_path, target, key_style, source)
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

    custom = _custom_terms(settings.glossary)
    if brief is None and len(results) < total and not stopped():
        if log:
            log("Dịch: AI đọc cả kịch bản, soạn hồ sơ (người nói, xưng hô, tên riêng, câu lặp)…")
        try:
            brief = make_brief(router, cues, lines, speakers, fit, src_lang=src_lang, tgt_lang=tgt_lang, style=style,
                               custom_terms=custom, log=log)
        except (ModelUnavailable, RenderCancelled):
            raise
        if brief and on_brief:
            on_brief(brief)
    brief = brief or ""
    brief_terms, canon = parse_brief(brief)
    # lời dặn kèm hồ sơ cho các bước phụ (dịch từng lô, khớp độ dài) — khoá bộ nhớ dịch vẫn theo lời dặn gốc
    style_b = (style + "\n\nScript brief (follow it — pronouns, names, repeated lines):\n" + brief).strip() if brief else style
    unsure: set[int] = set()                            # câu AI không trả nổi → giữ lời gốc, không ghi bộ nhớ dịch
    weak: set[int] = set()                              # câu dùng tạm bản dịch sát → không nhớ, lần sau dịch thoát lại
    directs: dict[int, str] = {}                        # bản dịch sát từng câu — chuẩn «đủ ý» cho bước khớp độ dài

    def keep(i: int, text: str) -> None:
        results[i] = text
        if i not in unsure:                             # câu giữ lời gốc: lần sau dịch lại, không nhớ
            ckpt.done[str(i)] = text
            if i not in weak:
                memory.put(target, lines[i], text, key_style)

    def halt() -> None:
        ckpt.save(total)
        memory.save()

    # 1) cả video một lượt (mỗi phần ≤ WHOLE_LINES câu): AI đọc hết câu chuyện rồi trả từng câu (dịch sát + dịch thoát)
    #    + thuật ngữ — xưng hô, tên gọi nhất quán, hiểu câu đùa theo cả video; video 2 phút = 1 lượt gọi thay vì ~10
    todo = [i for i in range(total) if i not in results]
    parts = _windows(todo, lines)
    fallback: list[int] = []
    for pi, idx in enumerate(parts):
        if stopped():
            halt()
            raise RenderCancelled("Đã huỷ dịch — tiến độ đã lưu, mở lại để tiếp tục.")
        if log:
            log(f"Dịch cả video một lượt: {len(idx)} câu" + (f" (phần {pi + 1}/{len(parts)})" if len(parts) > 1 else "")
                + "…")
        budgets = [syllable_range(cues, i, fit) for i in idx] if fit else None
        try:
            terms_got, got = _translate_whole(router, idx, lines, budgets, src_lang=src_lang, tgt_lang=tgt_lang,
                                              style=style, custom_terms=custom,
                                              known_terms=[t for t in ckpt.extra.get("terms", []) + brief_terms
                                                           if t not in custom],
                                              done=results, brief=brief, speakers=speakers)
        except (ModelUnavailable, RenderCancelled):
            halt()
            raise
        except ValueError as exc:                       # thiếu câu / JSON hỏng 3 lần → phần này dịch từng lô như cũ
            logger.warning("Dịch cả lượt phần %d lỗi (%s) → dịch từng lô", pi + 1, exc)
            if log:
                log("⚠ Dịch cả lượt chưa được (AI trả thiếu câu) — phần này dịch theo từng lô.")
            fallback += idx
            continue
        if terms_got and "terms" not in ckpt.extra:
            ckpt.extra = {**ckpt.extra, "terms": terms_got + custom}
            if log:
                log(f"Dịch: {len(terms_got)} thuật ngữ — " + ", ".join(f"{t['src']}={t['tgt']}" for t in terms_got[:8]))
        for i in idx:
            sat, free = got[i]
            directs[i] = sat
            if _free_broken(free, sat):
                logger.warning("Dịch cả lượt câu %d: dịch thoát cụt / thiếu (%r) → dùng bản dịch sát", i + 1, free)
                weak.add(i)
                keep(i, sat)
            else:
                keep(i, str(free).replace("\n", " ").strip())
        ckpt.save(total)
        if on_progress:
            on_progress(len(results) / max(1, total))
        if log:
            log(f"Dịch: xong {len(results)}/{total} câu — một lượt ({router.current.provider}/{router.current.model})")

    # 2) phần dịch cả lượt không được → từng lô 10 câu như VideoLingo: tóm tắt + thuật ngữ, dịch sát → dịch thoát
    if fallback:
        if "theme" not in ckpt.extra:
            if log:
                log("Dịch: tóm tắt nội dung + lấy thuật ngữ…")
            try:
                summ = _ask(router, summary_prompt(src_lang, tgt_lang, " ".join(lines)[:SUMMARY_CHARS], custom),
                            lambda d: isinstance(d.get("terms"), list) and all(
                                isinstance(t, dict) and {"src", "tgt", "note"} <= set(t) for t in d["terms"]))
                ckpt.extra = {"theme": str(summ.get("theme", "")), "terms": summ["terms"] + custom}
                ckpt.save(total)
                if log:
                    log(f"Dịch: {len(summ['terms'])} thuật ngữ — "
                        + ", ".join(f"{t['src']}={t['tgt']}" for t in summ["terms"][:8]))
            except (ModelUnavailable, RenderCancelled):
                halt()
                raise
            except ValueError as exc:                   # phụ: không lấy được thì dịch tiếp với từ điển của bạn
                logger.warning("VideoLingo: không lấy được tóm tắt / thuật ngữ (%s)", exc)
                if log:
                    log("⚠ Không lấy được thuật ngữ của video — vẫn dịch, chỉ dùng từ điển khi dịch.")
        theme, terms = ckpt.extra.get("theme", ""), ckpt.extra.get("terms", custom)

        def direct(idx: list[int], sp: str) -> dict[int, str]:
            """Dịch sát ``idx``; AI bỏ sót / gộp dòng 3 lần → chia đôi lô dịch lại, còn một câu vẫn lỗi thì giữ lời gốc."""
            keys = [str(n) for n in range(1, len(idx) + 1)]
            try:
                d = _ask(router, faith_prompt("\n".join(lines[i] for i in idx), sp, src_lang, tgt_lang),
                         lambda d: all(isinstance(d.get(k), dict) and not cut_at_quote(d[k].get("direct"))
                                       for k in keys))
                return {i: str(d[k]["direct"]).replace("\n", " ").strip() for i, k in zip(idx, keys)}
            except ValueError as exc:
                if len(idx) > 1:
                    logger.warning("VideoLingo: lô %d câu dịch sát lỗi (%s) → chia đôi dịch lại", len(idx), exc)
                    half = len(idx) // 2
                    return {**direct(idx[:half], sp), **direct(idx[half:], sp)}
                logger.warning("VideoLingo: câu %d không dịch được (%s) → giữ lời gốc", idx[0] + 1, exc)
                unsure.add(idx[0])
                return {idx[0]: lines[idx[0]]}

        chunks = [[fallback[j] for j in ch] for ch in _chunks([lines[i] for i in fallback])]
        for ci, idx in enumerate(chunks):
            if stopped():
                halt()
                raise RenderCancelled("Đã huỷ dịch — tiến độ đã lưu, mở lại để tiếp tục.")
            chunk = "\n".join(lines[i] for i in idx)
            prev = lines[max(0, idx[0] - 3):idx[0]] or None
            after = lines[idx[-1] + 1:idx[-1] + 3] or None
            sp = shared_prompt(prev, after, theme, _note(terms, chunk))
            keys = [str(n) for n in range(1, len(idx) + 1)]
            try:
                sat = direct(idx, sp)
            except (ModelUnavailable, RenderCancelled):
                halt()
                raise
            faith = {k: {"origin": lines[i], "direct": sat[i]} for i, k in zip(idx, keys)}
            directs.update({i: sat[i] for i in idx if i not in unsure})
            try:
                if any(i in unsure for i in idx):
                    raise ValueError("lô có câu chưa dịch được")
                budgets = {k: syllable_range(cues, i, fit) for i, k in zip(idx, keys)} if fit else None
                expr = _ask(router, express_prompt(faith, chunk, sp, src_lang, tgt_lang, style_b, budgets),
                            lambda d: all(isinstance(d.get(k), dict) for k in keys))
                out = []
                for k in keys:
                    free, sat_k = as_text(expr[k].get("free")), as_text(faith[k]["direct"])
                    if _free_broken(free, sat_k):
                        logger.warning("VideoLingo lô %d câu %s: dịch thoát cụt / thiếu (%r) → dùng bản dịch sát",
                                       ci + 1, k, free)
                        out.append(sat_k)
                        weak.add(idx[int(k) - 1])
                    else:
                        out.append(str(free).replace("\n", " ").strip())
            except (ModelUnavailable, RenderCancelled):
                halt()
                raise
            except ValueError as exc:                   # như bản gốc: bước dịch thoát lỗi → giữ bản dịch sát
                logger.warning("VideoLingo lô %d: dịch thoát lỗi (%s) → dùng bản dịch sát", ci + 1, exc)
                out = [faith[k]["direct"] for k in keys]
                weak.update(idx)
            for i, t in zip(idx, out):
                keep(i, t)
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
        except (ModelUnavailable, RenderCancelled):
            raise
        except Exception as exc:                        # noqa: BLE001 — phụ
            logger.warning("Không dịch lại được câu sót chữ gốc: %s", exc)
        left = [i + 1 for i in range(total) if _leaks(texts[i], target)]
        if left and log:                                # vẫn sót → báo đúng câu để sửa tay (giọng đọc sẽ bỏ qua chữ đó)
            log(f"⚠ {len(left)} câu dịch còn sót chữ Hán / Nhật / Hàn: câu {', '.join(map(str, left[:12]))}"
                + ("…" if len(left) > 12 else "") + " — sửa tay ở tab Lời thoại.")
    # câu AI không trả nổi mà bước trên chưa cứu được (câu sót chữ Hán đã báo ở trên)
    kept = [i + 1 for i in sorted(unsure) if texts[i] == lines[i] and not _leaks(texts[i], target)]
    if kept and log:
        log(f"⚠ {len(kept)} câu AI dịch không ra, đang giữ lời gốc: câu {', '.join(map(str, kept[:12]))}"
            + ("…" if len(kept) > 12 else "") + " — sửa tay ở tab Lời thoại.")
    # 4) câu lặp (cùng lời gốc) → cùng một bản dịch (bản hồ sơ chốt / bản dùng nhiều nhất) — không còn 4 cách dịch
    #    cho cùng một câu hỏi lặp lại
    same = unify_repeats(lines, texts, canon, unsure)
    if same:
        for i in same:
            if i not in weak:
                memory.put(target, lines[i], texts[i], key_style)
        if log:
            log(f"Dịch: đồng bộ {len(same)} câu lặp lại (cùng lời gốc → cùng bản dịch).")
    # 5) khớp thời lượng lồng tiếng
    if fit and not stopped():
        texts = fit_texts(cues, texts, router, fit, log, style=style_b, tgt_lang=tgt_lang, directs=directs)
    memory.save()
    ckpt.finish()
    # câu gốc bị ngắt ở dấu phẩy (Qwen tách câu dài) → bản dịch cũng kết thúc bằng «,» → giọng đọc lên giọng lửng lơ,
    # nghe như dịch cụt → kết thúc bằng dấu chấm
    texts = [as_text(t) for t in texts]                  # lưới cuối: không để «{'id': …, 'text': …}» lọt vào phụ đề
    texts = [re.sub(r"\s*[,;:，；：]+\s*$", ".", t) if t else t for t in texts]
    return [Cue(c.index, c.start, c.end, wrap_lines(t), dict(c.meta)) for c, t in zip(cues, texts)]


def _slot(cues: list[Cue], i: int, fit: dict) -> float:
    """Thời gian đọc được của câu ``i`` (giây, trên dòng thời gian video ra): khung câu + mượn ≤ 1,5 s khoảng lặng sau,
    trừ khoảng nghỉ tối thiểu giữa hai câu."""
    vs = max(0.5, float(fit.get("video_speed", 1.0)))
    gap = max(0.0, float(fit.get("min_gap", 0.0)))
    c = cues[i]
    nxt = cues[i + 1].start if i + 1 < len(cues) else c.end + TOLERANCE
    avail = (c.end - c.start) / vs + min(TOLERANCE, max(0.0, nxt - c.end) / vs) - (gap if i + 1 < len(cues) else 0)
    return max(0.3, avail)


def syllable_budget(cues: list[Cue], i: int, fit: dict) -> int:
    """Số âm tiết tối đa đọc kịp trong khung câu ``i`` (chừa 10 % cho chỗ ngắt hơi)."""
    from app.services.tts.quality import SEC_PER_SYL

    ts = max(0.5, float(fit.get("tts_speed", 1.0)))
    return max(3, int(_slot(cues, i, fit) * ts / SEC_PER_SYL * 0.9))


def syllable_range(cues: list[Cue], i: int, fit: dict) -> tuple[int, int]:
    """Số âm tiết nên đọc ở câu ``i``: tối đa = đọc kịp khung (``syllable_budget``); tối thiểu = ``MIN_FILL`` thời gian
    người gốc nói câu đó — câu dịch ngắn hơn thì giọng xong sớm, khoảng lặng dài, nghe như dịch cụt."""
    from app.services.tts.quality import SEC_PER_SYL

    ts = max(0.5, float(fit.get("tts_speed", 1.0)))
    vs = max(0.5, float(fit.get("video_speed", 1.0)))
    hi = syllable_budget(cues, i, fit)
    lo = int((cues[i].end - cues[i].start) / vs * ts / SEC_PER_SYL * MIN_FILL)
    return max(2, min(lo, hi - 1)), hi


def adjust_prompt(items: list[tuple[str, str, int, int, int, str]], tgt_lang: str, style: str) -> str:
    """``items``: (câu gốc, bản dịch hiện tại, số âm tiết hiện tại, tối thiểu, tối đa, bản dịch sát) — các câu dài /
    ngắn quá so với thời gian nói, hoặc bỏ ý so với bản dịch sát, sửa chung một lượt."""
    rows = "\n".join(f"{n}. [{lo}-{hi} syllables] SOURCE: {src}"
                      + (f"\n   FAITHFUL (all the information): {sat}" if sat else "")
                      + f"\n   CURRENT ({cnt} syllables): {cur}"
                      for n, (src, cur, cnt, lo, hi, sat) in enumerate(items, 1))
    return f'''
## Role
You are a professional subtitle editor fitting a {tgt_lang} voice-over to the original speaking time.

## Task
Rewrite each CURRENT line so its number of syllables (one syllable = one spoken word / Vietnamese syllable) falls
inside its [min-max syllables] range:
- too SHORT, or missing information that FAITHFUL has: put back what was left out (names, numbers, protection
  level, consequences, the punchline) — never pad with filler;
- too LONG: cut filler words and redundant phrases, keep the key meaning and the punchline.
Keep the tone, slang, pronouns and names of the CURRENT line — the result must stay natural, fun spoken {tgt_lang}.
One output per item, same numbers.

{style_section(style)}## Items
{rows}

## Output in only JSON format and no other text
{{"items": [{{"id": 1, "text": "..."}}]}}

{QUOTE_RULE}
'''.strip()


def fit_texts(cues: list[Cue], texts: list[str], router: Router, fit: dict, log: Callable[[str], None] | None = None,
              style: str = "", tgt_lang: str = "Vietnamese", directs: dict[int, str] | None = None) -> list[str]:
    """Khớp độ dài + đủ ý: câu đọc không kịp khung (dài), chưa tới ``MIN_FILL`` thời gian người gốc nói (ngắn — khoảng
    lặng dài), hoặc ngắn hơn hẳn bản dịch sát (``MIN_MEANING`` — AI bỏ ý, hay gặp ở cuối lượt dài) → AI sửa CHUNG một
    lượt cho mọi câu đó, có bản dịch sát làm chuẩn. Lượt chung lỗi → câu dài rút gọn từng câu như cũ (``_trim_each``)."""
    from app.services.translate_router import _parse_items
    from app.services.tts.quality import expected_duration, syllable_count

    ts = max(0.5, float(fit.get("tts_speed", 1.0)))
    directs = directs or {}
    out = list(texts)
    todo: list[int] = []
    why: dict[int, str] = {}
    for i in range(len(cues)):
        lo, hi = syllable_range(cues, i, fit)
        n, sat = syllable_count(out[i]), directs.get(i, "")
        if expected_duration(out[i], ts) > _slot(cues, i, fit):
            why[i] = "long"
        elif n < lo * 0.85:
            why[i] = "short"
        elif sat and n < MIN_MEANING * syllable_count(sat) and n < hi:
            why[i] = "meaning"                            # đủ khung nhưng ngắn hơn hẳn bản dịch sát → bỏ ý
        if i in why:
            todo.append(i)
    if not todo:
        if log:
            log("Dịch: khớp thời lượng lồng tiếng — các câu đã vừa khung, đủ ý")
        return out
    items = [(cues[i].plain.replace("\n", " "), out[i], syllable_count(out[i]), *syllable_range(cues, i, fit),
              directs.get(i, "")) for i in todo]
    try:
        new = _parse_items(router.call(adjust_prompt(items, tgt_lang, style)), list(range(1, len(todo) + 1)))
    except RenderCancelled:
        raise
    except ModelUnavailable as exc:
        # đã dịch xong mà AI hết lượt ở bước phụ này → giữ bản dịch (không bỏ cả video)
        logger.warning("Hết lượt AI khi khớp độ dài: %s", exc)
        if log:
            log("⚠ AI hết lượt — bỏ bước rút gọn câu dài; câu đọc không kịp sẽ được đọc nhanh hơn.")
        return out
    except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
        logger.warning("Khớp độ dài chung một lượt lỗi (%s) → rút gọn từng câu dài", exc)
        return _trim_each(cues, out, router, fit, log)
    done = {"long": 0, "short": 0, "meaning": 0}
    for (i, (_src, cur, cnt, lo, hi, _sat)), t in zip(zip(todo, items), new):
        n = syllable_count(t) if t else 0
        if not t or n > hi:                             # trống / cụt (đã lọc) / vượt tối đa → giữ câu cũ
            continue
        # dài → phải ngắn đi · ngắn / bỏ ý → phải dài thêm (đã bù ý) — không nhận bản sửa ngược chiều
        if (n < cnt) if why[i] == "long" else (n > cnt):
            out[i] = t
            done[why[i]] += 1
    if log:
        log(f"Dịch: khớp thời lượng + đủ ý — sửa {sum(done.values())}/{len(todo)} câu trong 1 lượt "
            f"({done['short']} ngắn quá, {done['meaning']} bỏ ý, {done['long']} dài quá)")
    return out


def _trim_each(cues: list[Cue], texts: list[str], router: Router, fit: dict, log: Callable[[str], None] | None = None,
               ) -> list[str]:
    """check_len_then_trim của VideoLingo với ước lượng đúng nhịp đọc: thời gian có = khung câu + mượn ≤ 1,5 s khoảng
    lặng sau (trên dòng thời gian video ra, trừ khoảng nghỉ tối thiểu giữa hai câu). Ước lượng đọc lâu hơn → AI rút gọn
    (lời nhắc gốc, cho biết số giây); vẫn dài hơn 10 % → thêm một lượt chặt hơn (cho biết số tiếng tối đa)."""
    from app.services.tts.quality import SEC_PER_SYL, expected_duration, syllable_count

    ts = max(0.5, float(fit.get("tts_speed", 1.0)))
    out, trimmed = list(texts), 0
    for i in range(len(cues)):
        avail = _slot(cues, i, fit)
        text = out[i]
        if expected_duration(text, ts) <= avail:
            continue
        try:
            new = str(_ask(router, trim_prompt(text, round(avail, 2)), lambda d: not cut_at_quote(d.get("result")))
                      ["result"]).strip()
            if expected_duration(new, ts) > avail * 1.1:
                syl = max(3, int(avail * ts / SEC_PER_SYL))
                new2 = str(_ask(router, trim_prompt(new, round(avail, 2)) + f"\n\nThe result must be at most {syl} "
                                "syllables (words) long, keep the key meaning.",
                                lambda d: not cut_at_quote(d.get("result")))["result"]).strip()
                if syllable_count(new2) < syllable_count(new):
                    new = new2
        except RenderCancelled:
            raise
        except ModelUnavailable as exc:
            # đã dịch xong mà AI hết lượt ở bước phụ này → giữ bản dịch, câu dài để giọng đọc nhanh hơn (không bỏ cả video)
            logger.warning("Hết lượt AI khi rút gọn câu %d: %s", i + 1, exc)
            if log:
                log("⚠ AI hết lượt — bỏ bước rút gọn câu dài; câu đọc không kịp sẽ được đọc nhanh hơn.")
            break
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


# ── Gộp mẩu chữ in sẵn (OCR) thành câu ──────────────────────────────────

GROUP_MAX_PARTS = 4           # số dòng chữ tối đa trong một câu gộp
GROUP_MAX_GAP = 1.0           # hai dòng cách nhau quá ngần này giây (theo tiếng nói) → không gộp
GROUP_MAX_SPAN = 8.0          # câu gộp không dài quá ngần này giây
GROUP_WINDOW = 120            # mỗi lần gửi AI tối đa ngần này dòng
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_JOIN_PUNCT = "。！？，、；：…!?,.;:"


def group_prompt(lines: list[str], spans: list[tuple[float, float]], language: str, max_chars: int) -> str:
    rows = "\n".join(f"{i}. [{a:.1f}-{b:.1f}s] {t}" for i, (t, (a, b)) in enumerate(zip(lines, spans), 1))
    return f'''
## Role
You are a professional subtitle editor for {language} short videos.

## Task
The numbered lines were read (OCR) from the burned-in subtitles of a video, with the time each one is spoken.
Creators often split ONE spoken sentence across 2-3 consecutive subtitle lines. Group consecutive lines that belong to
the same sentence, so each group can be translated and voiced as one natural sentence.

1. Only group consecutive lines; keep the order; every line number appears exactly once.
2. Never put lines spoken by different people in one group (e.g. a question and its answer).
3. At most {GROUP_MAX_PARTS} lines and about {max_chars} characters per group; when unsure, keep a line alone.

## Lines
{rows}

## Output in only JSON format and no other text
```json
{{"groups": [[1], [2, 3], [4, 5]]}}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''.strip()


def _groups_ok(d: dict, n: int) -> bool:
    """Mỗi dòng 1..n đúng một lần, theo thứ tự (nhóm liền nhau)."""
    g = d.get("groups")
    if not isinstance(g, list) or not all(isinstance(x, list) and x for x in g):
        return False
    try:
        flat = [int(v) for x in g for v in x]
    except (TypeError, ValueError):
        return False
    return flat == list(range(1, n + 1))


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _fit_group(members: list[int], cues: list[Cue], texts: list[str], max_chars: int) -> list[list[int]]:
    """Nhóm AI đề xuất mà quá dài / cách quãng → cắt lại (không tin AI tuyệt đối)."""
    out: list[list[int]] = []
    cur: list[int] = []
    for k in members:
        if cur and (len(cur) >= GROUP_MAX_PARTS or cues[k].start - cues[cur[-1]].end > GROUP_MAX_GAP
                    or cues[k].end - cues[cur[0]].start > GROUP_MAX_SPAN
                    or sum(_chars(texts[j]) for j in cur) + _chars(texts[k]) > max_chars):
            out.append(cur)
            cur = []
        cur.append(k)
    if cur:
        out.append(cur)
    return out


def _join(parts: list[str], cjk: bool) -> str:
    text = parts[0]
    for part in parts[1:]:
        if cjk:
            text += ("" if text[-1:] in _JOIN_PUNCT else "，") + part
        else:
            text += " " + part
    return text


def group_fragments(cues: list[Cue], *, settings: Settings, language: str = "",
                    log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> list[Cue]:
    """Phụ đề in sẵn (OCR): người làm video hay cắt MỘT câu nói thành 2–3 dòng chữ → dịch / lồng tiếng từng mẩu thì
    câu vụn, khung đọc ngắn phải rút gọn nhiều. AI gộp các dòng liền nhau cùng một câu (không gộp lời hai người, không
    qua khoảng lặng dài) → câu trọn như khi nghe giọng nói, mà chữ vẫn đúng như trên hình (tên riêng). Không gọi được
    AI → giữ nguyên từng dòng."""
    if len(cues) < 2:
        return cues
    texts = [c.plain.replace("\n", " ").strip() for c in cues]
    cjk = sum(1 for t in texts if _CJK.search(t)) * 2 >= len(texts)
    max_chars = 20 if cjk else 70
    lang = source_name(texts, language)
    groups: list[list[int]] = []
    try:
        router = Router(settings, log, cancel)
        for a in range(0, len(cues), GROUP_WINDOW):
            if cancel is not None and cancel.is_set():
                raise RenderCancelled("Đã huỷ")
            part = list(range(a, min(len(cues), a + GROUP_WINDOW)))
            d = _ask(router, group_prompt([texts[i] for i in part], [(cues[i].start, cues[i].end) for i in part],
                                          lang, max_chars), lambda d: _groups_ok(d, len(part)))
            for g in d["groups"]:
                groups += _fit_group([a + int(v) - 1 for v in g], cues, texts, max_chars)
    except RenderCancelled:
        raise
    except Exception as exc:                            # noqa: BLE001 — hết lượt / mất mạng / AI trả sai dạng
        logger.warning("Gộp câu OCR lỗi (%s) → giữ từng dòng", exc)
        if log:
            log("Gộp mẩu chữ thành câu: không gọi được AI → giữ nguyên từng dòng chữ.")
        return cues
    out = [Cue(n + 1, cues[g[0]].start, cues[g[-1]].end, wrap_lines(_join([texts[i] for i in g], cjk)),
               dict(cues[g[0]].meta)) for n, g in enumerate(groups)]
    if log:
        log(f"Gộp mẩu chữ thành câu: {len(cues)} dòng chữ → {len(out)} câu")
    return out


_SPLIT_AI_DOWN = 0.0          # AI tách câu hỏng lúc nào → tới mốc này các video sau trong lô cắt ở dấu câu luôn


def split_long_sentences(sentences: list[str], token_counts: list[int], *, settings: Settings, language: str = "zh",
                         log: Callable[[str], None] | None = None,
                         cancel: threading.Event | None = None) -> list[str]:
    """Câu > 20 token (spaCy) → AI tách thành ⌈token/20⌉ phần (chỉ lấy VỊ TRÍ cắt, giữ nguyên chữ gốc). Không gọi
    được AI (hết lượt…) → cắt máy ở dấu câu gần giữa nhất như bản gốc."""
    global _SPLIT_AI_DOWN
    joiner = "" if language in ("zh", "ja", "yue") else " "
    out: list[str] = []
    router = None
    # hết lượt / chưa có key → các câu sau cắt máy luôn, khỏi chờ; vừa hỏng ở video trước (≤ 10 phút) cũng vậy
    use_ai = time.time() >= _SPLIT_AI_DOWN
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("Đã huỷ")
    long_n = sum(1 for n in token_counts if n > MAX_SPLIT_TOKENS)
    if long_n and use_ai and log:                       # model có suy luận mất cả phút mỗi câu → báo kẻo tưởng treo
        log(f"Tách {long_n} câu dài bằng AI…")
    for s, n in zip(sentences, token_counts):
        if n <= MAX_SPLIT_TOKENS:
            out.append(s)
            continue
        parts_n = math.ceil(n / MAX_SPLIT_TOKENS)
        if not use_ai:
            out.extend(_mechanical_split(s, parts_n))
            continue
        try:
            router = router or Router(settings, log, cancel)
            d = _ask(router, split_prompt(s, parts_n, MAX_SPLIT_TOKENS, language), lambda d: _pick_split(d) is not None)
            pts = _split_positions(s, _pick_split(d), joiner)
            pieces, last = [], 0
            for p in pts:
                pieces.append(s[last:p].strip())
                last = p
            pieces.append(s[last:].strip())
            pieces = [x for x in pieces if x]
            out.extend(pieces if len(pieces) > 1 else _mechanical_split(s, parts_n))
        except RenderCancelled:
            raise
        except Exception as exc:                        # noqa: BLE001 — một câu tách hỏng không được dừng cả video
            logger.warning("AI tách câu dài lỗi (%s) → cắt ở dấu câu", exc)
            if router is None or isinstance(exc, RuntimeError):     # hết lượt / mất mạng / key hỏng (JSON hỏng = ValueError)
                use_ai = False
                _SPLIT_AI_DOWN = time.time() + 600
                if log:
                    log("Tách câu dài: không gọi được AI → cắt ở dấu câu cho các câu còn lại.")
            out.extend(_mechanical_split(s, parts_n))
    return out
