"""Dự án Story: cảnh, nhân vật, metadata — lưu JSON trong output/projects/<slug>/."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config.paths import OUTPUT_DIR

PROJECTS_DIR = OUTPUT_DIR / "projects"


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:48] or f"story-{int(time.time())}"


@dataclass
class Scene:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    narration: str = ""
    image_prompt: str = ""
    speaker: str = "Người kể"
    sfx: str = ""
    image_path: str = ""
    video_path: str = ""
    audio_path: str = ""
    duration: float = 0.0
    notes: str = ""

    @property
    def visual(self) -> str:
        return self.video_path or self.image_path


@dataclass
class Character:
    name: str
    description: str = ""
    voice_provider: str = "Edge TTS"
    voice_id: str = ""
    voice_name: str = ""
    speed: float = 1.0


@dataclass
class StoryProject:
    name: str = "Câu chuyện mới"
    topic: str = ""
    language: str = "Tiếng Việt"
    length: str = "medium"
    tone: str = ""
    genre: str = ""
    competitor: str = ""
    master_prompt_preset: str = "cinematic_hyperrealism"
    master_prompt_custom: str = ""
    ratio: str = "16:9"
    characters: list[Character] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    chapters: str = ""
    thumbnail_prompt: str = ""
    outline: str = ""
    assembled_path: str = ""
    srt_path: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    path: str = ""             # thư mục dự án (không lưu vào JSON)

    # ── Thư mục ──────────────────────────────────────────────────────────
    @property
    def dir(self) -> Path:
        if not self.path:
            self.path = str(PROJECTS_DIR / slugify(self.name))
        return Path(self.path)

    def subdir(self, name: str) -> Path:
        d = self.dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Nhân vật ─────────────────────────────────────────────────────────
    def character(self, name: str) -> Character | None:
        for c in self.characters:
            if c.name.strip().lower() == (name or "").strip().lower():
                return c
        return None

    def ensure_character(self, name: str, description: str = "") -> Character:
        c = self.character(name)
        if c is None:
            c = Character(name=name.strip() or "Người kể", description=description)
            self.characters.append(c)
        return c

    def speakers(self) -> list[str]:
        names = ["Người kể"]
        for c in self.characters:
            if c.name not in names:
                names.append(c.name)
        for s in self.scenes:
            if s.speaker and s.speaker not in names:
                names.append(s.speaker)
        return names

    # ── Thống kê ─────────────────────────────────────────────────────────
    @property
    def total_chars(self) -> int:
        return sum(len(s.narration) for s in self.scenes)

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def progress_summary(self) -> str:
        n = len(self.scenes)
        if n == 0:
            return "Chưa có cảnh."
        voiced = sum(1 for s in self.scenes if s.audio_path and Path(s.audio_path).exists())
        visual = sum(1 for s in self.scenes if s.visual and Path(s.visual).exists())
        return (f"{n} cảnh · {self.total_chars:,} ký tự · giọng {voiced}/{n} · hình {visual}/{n}"
                + (f" · ~{self.total_duration / 60:.1f} phút" if self.total_duration else ""))

    # ── Lưu / nạp ─────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d

    def save(self) -> Path:
        self.updated_at = time.time()
        d = self.dir
        d.mkdir(parents=True, exist_ok=True)
        p = d / "project.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return p

    @classmethod
    def load(cls, path: str | Path) -> StoryProject:
        p = Path(path)
        if p.is_dir():
            p = p / "project.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        proj = cls()
        for k, v in data.items():
            if k == "scenes":
                proj.scenes = [Scene(**{kk: vv for kk, vv in s.items() if kk in Scene.__dataclass_fields__})
                               for s in v if isinstance(s, dict)]
            elif k == "characters":
                proj.characters = [Character(**{kk: vv for kk, vv in c.items() if kk in Character.__dataclass_fields__})
                                   for c in v if isinstance(c, dict)]
            elif k in cls.__dataclass_fields__ and k != "path":
                setattr(proj, k, v)
        proj.path = str(p.parent)
        return proj

    @staticmethod
    def list_projects() -> list[Path]:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        out = [p for p in PROJECTS_DIR.iterdir() if (p / "project.json").exists()]
        out.sort(key=lambda p: (p / "project.json").stat().st_mtime, reverse=True)
        return out
