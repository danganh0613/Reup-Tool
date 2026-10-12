"""Mô tả 5 lệnh cho AI (MCP ``tools/list`` và function-calling của DeepSeek) — tiếng Anh, ngắn: mô tả tool được gửi lại
mỗi lượt nên mỗi chữ đều tốn token (cả bộ ~600 token)."""

from __future__ import annotations

TOOLS: list[dict] = [
    {"name": "videos",
     "description": "List the Edit Studio queue (short id, status, flags = lines needing a listen). "
                    "Optionally add video files / folders, applying a template (preset) to them.",
     "inputSchema": {"type": "object", "properties": {
         "add": {"type": "array", "items": {"type": "string"}, "description": "video file or folder paths"},
         "preset": {"type": "string", "description": "template name (see settings().templates)"},
         "limit": {"type": "integer"}}}},
    {"name": "run",
     "description": "prepare = recognize -> script brief -> translate -> dub (stops where `until` says); "
                    "render = export prepared videos; wait = just wait. Waits server-side up to wait_s.",
     "inputSchema": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": "string"}, "description": "video ids; omit for all"},
         "action": {"type": "string", "enum": ["prepare", "render", "wait"]},
         "until": {"type": "string", "enum": ["all", "source", "translation"],
                   "description": "all = full auto; source = stop after transcript; translation = stop before dubbing"},
         "wait_s": {"type": "integer", "description": "max seconds to wait (<=3600)"}}}},
    {"name": "review",
     "description": "Lines of one video. Default: only flagged lines (why: misread voice, too fast, untranslated, "
                    "pronoun vs script brief) + the brief's speaker pronouns. all=true pages through every line.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": {"type": "string"}, "all": {"type": "boolean"}, "limit": {"type": "integer"},
         "offset": {"type": "integer"}}}},
    {"name": "fix",
     "description": "Edit lines of one video: `text` {line number: new line} and/or `request` (natural-language "
                    "instruction the app's own AI applies with the script brief). voice=true re-dubs changed lines.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": {"type": "string"}, "text": {"type": "object", "additionalProperties": {"type": "string"}},
         "request": {"type": "string"}, "voice": {"type": "boolean"}, "wait_s": {"type": "integer"}}}},
    {"name": "settings",
     "description": "Get/set shared settings: template, output frame (ratio, fit, offset_x/offset_y -50..50, zoom "
                    "100..400), file size (size), translation target + style, until. No args = read only.",
     "inputSchema": {"type": "object", "properties": {
         "template": {"type": "string"},
         "ratio": {"type": "string", "enum": ["goc", "9:16", "16:9", "1:1", "4:5", "3:4", "4:3", "21:9"]},
         "fit": {"type": "string", "enum": ["nen_mo", "vien_den", "cat"],
                 "description": "nen_mo = blurred bars above/below, vien_den = black bars, cat = crop to fill"},
         "offset_x": {"type": "integer"}, "offset_y": {"type": "integer"}, "zoom": {"type": "integer"},
         "size": {"type": "string", "enum": ["social", "small", "max"],
                  "description": "social = capped bitrate for TikTok/Reels/Shorts/FB, small = H.265 half, max = no cap"},
         "target": {"type": "string"}, "style": {"type": "string"},
         "until": {"type": "string", "enum": ["all", "source", "translation"]}}}},
]


def openai_tools() -> list[dict]:
    """Cùng bộ lệnh theo định dạng function-calling (DeepSeek / OpenAI)."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["inputSchema"]}} for t in TOOLS]
