"""Viết kịch bản, metadata YouTube và prompt ảnh bằng Gemini."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.services.gemini_client import GeminiClient
from app.services.story.project import Character, Scene, StoryProject
from app.services.story.prompts import LENGTHS, competitor_brief, load_competitors, master_prompt_text

logger = logging.getLogger(__name__)

SFX_LIST = ["", "whoosh", "impact", "thunder", "door_creak", "footsteps", "heartbeat", "clock_tick",
            "glass_shatter", "chime", "riser", "ambient_drone", "rain", "wind", "crowd", "laugh"]

_SYSTEM = (
    "You are an award-winning YouTube storytelling scriptwriter and showrunner. You write immersive, "
    "emotionally engaging narration that keeps viewers watching until the end. You always answer with valid JSON only."
)


def _lang_note(language: str) -> str:
    return f"Write ALL narration and dialogue in {language}. Image prompts must be in English."


def generate_outline(client: GeminiClient, model: str, proj: StoryProject) -> dict:
    label, chars, n_scenes = LENGTHS.get(proj.length, LENGTHS["medium"])
    n_chapters = max(1, min(12, round(chars / 3500)))
    comp = load_competitors().get(proj.competitor) if proj.competitor else None
    brief = competitor_brief(comp)
    prompt = f"""Create a story outline for a YouTube narration video.
{_lang_note(proj.language)}
Topic / idea from the creator: {proj.topic or '(choose an engaging original idea that fits the channel style)'}
Tone: {proj.tone or 'engaging'}
Genre: {proj.genre or comp.get('genre_topic', '') if comp else proj.genre}
{('Channel style guide:' + chr(10) + brief) if brief else ''}
Target total narration length: about {chars:,} characters, split into {n_chapters} chapters of similar length.
Design 2–4 memorable characters with distinct names and a short visual description (age, look, clothing) that will be reused in every image prompt for consistency.
Return JSON:
{{"title": "...", "logline": "one sentence hook", "characters": [{{"name": "...", "description": "...", "voice_hint": "male/female, age, personality"}}],
 "chapters": [{{"title": "...", "summary": "3-5 sentences of what happens", "hook": "cliffhanger or emotional beat at the end"}}]}}"""
    data = client.generate_json(model, prompt, system=_SYSTEM, temperature=0.9)
    if not isinstance(data, dict) or not data.get("chapters"):
        raise RuntimeError("Gemini không trả outline hợp lệ")
    return data


def expand_chapter(client: GeminiClient, model: str, proj: StoryProject, outline: dict, index: int,
                   chars_per_chapter: int, recap: str) -> list[Scene]:
    chapters = outline.get("chapters", [])
    ch = chapters[index]
    n = len(chapters)
    chars_desc = "\n".join(f"- {c.get('name')}: {c.get('description')}" for c in outline.get("characters", []))
    style = master_prompt_text(proj.master_prompt_preset, proj.master_prompt_custom)
    prompt = f"""Story title: {outline.get('title', proj.name)}
Logline: {outline.get('logline', '')}
Characters (keep names and looks consistent):
{chars_desc}
{('Recap of previous chapters: ' + recap) if recap else 'This is the first chapter.'}

Now write chapter {index + 1}/{n}: "{ch.get('title', '')}".
Chapter summary: {ch.get('summary', '')}
End of chapter beat: {ch.get('hook', '')}
{_lang_note(proj.language)}
Tone: {proj.tone or 'engaging'}.
Length: about {chars_per_chapter:,} characters of narration in total for this chapter.
Split the chapter into scenes. Each scene = 2–5 sentences of narration that flow naturally when read aloud.
"speaker" is "Người kể" for narration, or a character's name when the scene is that character speaking in first person.
"image_prompt": ONE detailed English prompt for a single still image of this scene — subject, action, setting, lighting, camera angle, mood. Mention the characters by their visual description (never by name only). Visual style to append: {style or 'cinematic'}.
"sfx": optional sound effect from {SFX_LIST[1:]} or "".
Return JSON: {{"scenes": [{{"narration": "...", "speaker": "Người kể", "image_prompt": "...", "sfx": ""}}]}}"""
    data = client.generate_json(model, prompt, system=_SYSTEM, temperature=0.9)
    scenes_raw = data.get("scenes") if isinstance(data, dict) else data
    out: list[Scene] = []
    for s in scenes_raw or []:
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration", "")).strip()
        if not narration:
            continue
        out.append(Scene(narration=narration, image_prompt=str(s.get("image_prompt", "")).strip(),
                         speaker=str(s.get("speaker", "Người kể")).strip() or "Người kể",
                         sfx=str(s.get("sfx", "")).strip()))
    if not out:
        raise RuntimeError(f"Chương {index + 1} không có cảnh nào")
    return out


def write_script(client: GeminiClient, model: str, proj: StoryProject, *,
                 on_progress: Callable[[float], None] | None = None,
                 log: Callable[[str], None] | None = None,
                 cancel: threading.Event | None = None) -> StoryProject:
    """Outline → từng chương → cảnh. Ghi thẳng vào ``proj``."""
    if log:
        log("Đang lên dàn ý…")
    outline = generate_outline(client, model, proj)
    proj.title = outline.get("title", proj.title) or proj.name
    proj.outline = "\n".join(f"{i + 1}. {c.get('title', '')}: {c.get('summary', '')}"
                             for i, c in enumerate(outline.get("chapters", [])))
    proj.characters = [Character(name=str(c.get("name", "")).strip() or f"Nhân vật {i + 1}",
                                 description=str(c.get("description", "")).strip())
                       for i, c in enumerate(outline.get("characters", [])) if isinstance(c, dict)]
    _, chars, _ = LENGTHS.get(proj.length, LENGTHS["medium"])
    chapters = outline.get("chapters", [])
    per_chapter = max(800, chars // max(1, len(chapters)))
    scenes: list[Scene] = []
    recap = ""
    for i, ch in enumerate(chapters):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Đã huỷ viết kịch bản")
        if log:
            log(f"Viết chương {i + 1}/{len(chapters)}: {ch.get('title', '')}")
        part = expand_chapter(client, model, proj, outline, i, per_chapter, recap)
        scenes.extend(part)
        recap = (recap + " " if recap else "") + f"Chapter {i + 1} ({ch.get('title', '')}): {ch.get('summary', '')}"
        recap = recap[-2500:]
        if on_progress:
            on_progress((i + 1) / max(1, len(chapters)))
    proj.scenes = scenes
    if log:
        log(f"Kịch bản xong: {len(scenes)} cảnh · {proj.total_chars:,} ký tự")
    return proj


def regenerate_scene_text(client: GeminiClient, model: str, proj: StoryProject, scene: Scene,
                          instruction: str = "") -> Scene:
    """Viết lại lời kể / prompt ảnh của một cảnh theo yêu cầu."""
    style = master_prompt_text(proj.master_prompt_preset, proj.master_prompt_custom)
    prompt = f"""Story: {proj.title or proj.name}. Language of narration: {proj.language}. Tone: {proj.tone}.
Characters: {'; '.join(f'{c.name}: {c.description}' for c in proj.characters)}
Current scene narration: {scene.narration}
Current image prompt: {scene.image_prompt}
Instruction from the creator: {instruction or 'Improve the flow and make the scene more vivid, same length.'}
Return JSON: {{"narration": "...", "image_prompt": "... (English, append style: {style})", "speaker": "{scene.speaker}"}}"""
    data = client.generate_json(model, prompt, system=_SYSTEM, temperature=0.9)
    if isinstance(data, dict):
        scene.narration = str(data.get("narration", scene.narration)).strip() or scene.narration
        scene.image_prompt = str(data.get("image_prompt", scene.image_prompt)).strip() or scene.image_prompt
    return scene


def generate_metadata(client: GeminiClient, model: str, proj: StoryProject, *, timeline: str = "") -> dict:
    script_excerpt = "\n".join(s.narration for s in proj.scenes)[:12000]
    prompt = f"""Create YouTube metadata for this narration video, in {proj.language}.
Title of the story: {proj.title or proj.name}
Script (excerpt): {script_excerpt}
{('Scene timeline (for chapters): ' + chr(10) + timeline) if timeline else ''}
Return JSON: {{"titles": ["5 click-worthy but honest title options (<= 70 chars)"], "description": "engaging description with 2 paragraphs, a call to action and 3-5 hashtags", "tags": ["15-25 tags"], "chapters": "YouTube chapter list, one per line 'MM:SS Title' (start with 00:00)", "thumbnail_prompt": "English prompt for a thumbnail image with a strong emotion and a short text overlay idea"}}"""
    data = client.generate_json(model, prompt, system=_SYSTEM, temperature=0.8)
    if not isinstance(data, dict):
        raise RuntimeError("Metadata không hợp lệ")
    titles = data.get("titles") or []
    proj.title = str(titles[0]) if titles else proj.title
    proj.description = str(data.get("description", ""))
    proj.tags = [str(t) for t in (data.get("tags") or [])]
    proj.chapters = str(data.get("chapters", ""))
    proj.thumbnail_prompt = str(data.get("thumbnail_prompt", ""))
    data["_titles"] = titles
    return data


def improve_prompt(client: GeminiClient, model: str, idea: str, style: str) -> str:
    """Trình dựng prompt: biến mô tả thô thành prompt ảnh chi tiết."""
    data = client.generate_json(model, f"""Turn this rough idea into ONE detailed English image-generation prompt (subject, action, setting, lighting, lens, mood), under 90 words, then append this style: {style}.
Idea: {idea}
Return JSON: {{"prompt": "..."}}""", system=_SYSTEM, temperature=0.7)
    return str(data.get("prompt", idea)) if isinstance(data, dict) else idea
