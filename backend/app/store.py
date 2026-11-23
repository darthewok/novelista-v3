from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import data_dir
from app.enums import Audience, BookFormat, BookStatus, ChapterStatus, POV, Tense, WritingStyle
from app.formats import get_profile
from app.storage import atomic_write_json, atomic_write_text, read_json, read_text

log = logging.getLogger("novelista.store")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FrontMatter:
    subtitle: str = ""
    author_name: str = ""
    series_name: str = ""
    series_number: str = ""
    copyright_year: str = ""
    imprint: str = ""
    dedication: str = ""
    epigraph: str = ""
    acknowledgments: str = ""
    author_bio: str = ""
    also_by: str = ""
    blurb: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "FrontMatter":
        if not isinstance(d, dict):
            return cls()
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: str(v or "") for k, v in d.items() if k in allowed})


@dataclass
class ChapterRec:
    number: int
    title: str
    status: ChapterStatus = ChapterStatus.pending
    word_count: int = 0
    version: int = 0
    tells: int = 0
    error_count: int = 0
    warning_count: int = 0
    cleanliness_grade: str = ""
    pov: str = ""
    scene_count: int = 0
    has_illustration_prompt: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChapterRec":
        return cls(
            number=int(d["number"]),
            title=str(d.get("title") or f"Chapter {d['number']}"),
            status=ChapterStatus(d.get("status", ChapterStatus.pending.value)),
            word_count=int(d.get("word_count") or 0),
            version=int(d.get("version") or 0),
            tells=int(d.get("tells") or 0),
            error_count=int(d.get("error_count") or 0),
            warning_count=int(d.get("warning_count") or 0),
            cleanliness_grade=str(d.get("cleanliness_grade") or ""),
            pov=str(d.get("pov") or ""),
            scene_count=int(d.get("scene_count") or 0),
            has_illustration_prompt=bool(d.get("has_illustration_prompt", False)),
        )


@dataclass
class Book:
    id: str
    title: str
    status: BookStatus = BookStatus.idea
    book_format: BookFormat = BookFormat.novel
    audience: Audience = Audience.adult
    chapter_count: int | None = None
    target_words: int | None = None
    pov: POV = POV.third_limited
    tense: Tense = Tense.past
    writing_style: WritingStyle = WritingStyle.literary_contemporary
    custom_style: str = ""
    genre: str = ""
    language: str = "English"
    idea: str = ""
    source_mode: str = "wizard"
    obsidian_vault_path: str = ""
    front_matter: FrontMatter = field(default_factory=FrontMatter)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)
    chapters: list[ChapterRec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "book_format": self.book_format.value,
            "audience": self.audience.value,
            "chapter_count": self.chapter_count,
            "target_words": self.target_words,
            "pov": self.pov.value,
            "tense": self.tense.value,
            "writing_style": self.writing_style.value,
            "custom_style": self.custom_style,
            "genre": self.genre,
            "language": self.language,
            "idea": self.idea,
            "source_mode": self.source_mode,
            "obsidian_vault_path": self.obsidian_vault_path,
            "front_matter": asdict(self.front_matter),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chapters": [c.to_dict() for c in sorted(self.chapters, key=lambda c: c.number)],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Book":
        raw_mode = str(d.get("source_mode") or "wizard").strip().lower()
        mode = raw_mode if raw_mode in {"wizard", "obsidian"} else "wizard"
        return cls(
            id=str(d["id"]),
            title=str(d.get("title") or "Untitled"),
            status=BookStatus(d.get("status", BookStatus.idea.value)),
            book_format=BookFormat(d.get("book_format", BookFormat.novel.value)),
            audience=Audience(d.get("audience", Audience.adult.value)),
            chapter_count=d.get("chapter_count"),
            target_words=d.get("target_words"),
            pov=POV(d.get("pov", POV.third_limited.value)),
            tense=Tense(d.get("tense", Tense.past.value)),
            writing_style=WritingStyle(d.get("writing_style", WritingStyle.literary_contemporary.value)),
            custom_style=str(d.get("custom_style") or ""),
            genre=str(d.get("genre") or ""),
            language=str(d.get("language") or "English"),
            idea=str(d.get("idea") or ""),
            source_mode=mode,
            obsidian_vault_path=str(d.get("obsidian_vault_path") or ""),
            front_matter=FrontMatter.from_dict(d.get("front_matter")),
            created_at=str(d.get("created_at") or utcnow_iso()),
            updated_at=str(d.get("updated_at") or utcnow_iso()),
            chapters=[ChapterRec.from_dict(c) for c in d.get("chapters", [])],
        )

    def get_chapter(self, number: int) -> ChapterRec | None:
        for c in self.chapters:
            if c.number == number:
                return c
        return None


_BOOK_LOCKS: dict[str, asyncio.Lock] = {}


def book_lock(book_id: str) -> asyncio.Lock:
    lk = _BOOK_LOCKS.get(book_id)
    if lk is None:
        lk = asyncio.Lock()
        _BOOK_LOCKS[book_id] = lk
    return lk


def novels_dir() -> Path:
    p = data_dir() / "novels"
    p.mkdir(parents=True, exist_ok=True)
    return p


def book_dir(book_id: str) -> Path:
    # Ensure UUID/safe directory id
    if not book_id or "/" in book_id or "\\" in book_id or ".." in book_id:
        raise ValueError("Invalid book id")
    return novels_dir() / book_id


def ensure_book_dirs(bdir: Path) -> None:
    for sub in ("chapters", "briefs", "reports", "scenes", "uploads", "uploads/illustrations"):
        (bdir / sub).mkdir(parents=True, exist_ok=True)


def chapter_path(bdir: Path, n: int) -> Path:
    p3 = bdir / "chapters" / f"ch_{n:03d}.md"
    if p3.exists():
        return p3
    p2 = bdir / "chapters" / f"ch_{n:02d}.md"
    if p2.exists():
        return p2
    return p3


def chapter_version_path(bdir: Path, n: int, version: int) -> Path:
    return bdir / "chapters" / f"ch_{n:03d}.v{version}.md"


def brief_path(bdir: Path, n: int) -> Path:
    p3 = bdir / "briefs" / f"ch_{n:03d}.md"
    if p3.exists():
        return p3
    p2 = bdir / "briefs" / f"ch_{n:02d}.md"
    return p2 if p2.exists() else p3


def report_path(bdir: Path, n: int) -> Path:
    p3 = bdir / "reports" / f"ch_{n:03d}.json"
    if p3.exists():
        return p3
    p2 = bdir / "reports" / f"ch_{n:02d}.json"
    return p2 if p2.exists() else p3


def scene_path(bdir: Path, n: int) -> Path:
    return bdir / "scenes" / f"ch_{n:03d}.json"


def _load_book_unlocked(book_id: str) -> Book | None:
    p = book_dir(book_id) / "book.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return Book.from_dict(data)


def get_book(book_id: str) -> Book | None:
    try:
        return _load_book_unlocked(book_id)
    except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load book %s: %s", book_id, exc)
        return None


def list_books() -> list[Book]:
    out: list[Book] = []
    root = novels_dir()
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        bjson = entry / "book.json"
        if not bjson.exists():
            continue
        try:
            out.append(Book.from_dict(json.loads(bjson.read_text(encoding="utf-8"))))
        except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
            log.warning("Skipping corrupt book %s: %s", entry.name, exc)
    out.sort(key=lambda b: b.updated_at, reverse=True)
    return out


def derive_book_status(book: Book, bdir: Path | None = None) -> BookStatus:
    bdir = bdir or book_dir(book.id)
    chapters = book.chapters
    if chapters:
        statuses = {c.status for c in chapters}
        if all(s in (ChapterStatus.polished, ChapterStatus.accepted) for s in statuses):
            return BookStatus.ready
        if any(s != ChapterStatus.pending for s in statuses):
            if all(s != ChapterStatus.pending for s in statuses):
                return BookStatus.polishing
            return BookStatus.drafting
        return BookStatus.planning
    for fname in ("outline.md", "world.md", "characters.md", "voice.md", "canon.md"):
        if (bdir / fname).exists() and read_text(bdir / fname).strip():
            return BookStatus.planning
    return BookStatus.idea


def save_book(book: Book) -> Book:
    bdir = book_dir(book.id)
    ensure_book_dirs(bdir)
    book.status = derive_book_status(book, bdir)
    book.updated_at = utcnow_iso()
    atomic_write_json(bdir / "book.json", book.to_dict())
    return book


def create_book(
    *,
    title: str = "Untitled",
    book_format: BookFormat = BookFormat.novel,
    audience: Audience | None = None,
    chapter_count: int | None = None,
    target_words: int | None = None,
    pov: POV = POV.third_limited,
    tense: Tense = Tense.past,
    writing_style: WritingStyle = WritingStyle.literary_contemporary,
    custom_style: str = "",
    genre: str = "",
    language: str = "English",
    idea: str = "",
    source_mode: str = "wizard",
    obsidian_vault_path: str = "",
    front_matter: FrontMatter | None = None,
) -> Book:
    prof = get_profile(book_format)
    bid = str(uuid.uuid4())
    mode = source_mode if source_mode in {"wizard", "obsidian"} else "wizard"
    b = Book(
        id=bid,
        title=(title or "Untitled").strip() or "Untitled",
        book_format=book_format,
        audience=audience or prof.default_audience,
        chapter_count=chapter_count if chapter_count is not None else prof.default_units,
        target_words=target_words if target_words is not None else prof.default_words,
        pov=pov,
        tense=tense,
        writing_style=writing_style,
        custom_style=custom_style.strip(),
        genre=genre.strip(),
        language=(language or "English").strip() or "English",
        idea=(idea or "").strip(),
        source_mode=mode,
        obsidian_vault_path=(obsidian_vault_path or "").strip(),
        front_matter=front_matter or FrontMatter(),
    )
    bdir = book_dir(bid)
    ensure_book_dirs(bdir)
    if b.idea:
        atomic_write_text(bdir / "seed.txt", b.idea)
    return save_book(b)


def delete_book(book_id: str) -> bool:
    bdir = book_dir(book_id)
    if not bdir.exists():
        return False
    shutil.rmtree(bdir)
    _BOOK_LOCKS.pop(book_id, None)
    return True


def list_chapter_versions(bdir: Path, n: int) -> list[int]:
    ch_dir = bdir / "chapters"
    if not ch_dir.exists():
        return []
    versions: list[int] = []
    prefixes = (f"ch_{n:03d}.v", f"ch_{n:02d}.v")
    for f in ch_dir.iterdir():
        if not f.is_file():
            continue
        for pfx in prefixes:
            if f.name.startswith(pfx) and f.name.endswith(".md"):
                raw = f.name[len(pfx):-3]
                if raw.isdigit():
                    versions.append(int(raw))
    return sorted(set(versions))


def snapshot_chapter(bdir: Path, ch: ChapterRec) -> int | None:
    """Save current chapter text as v{ch.version} and bump ch.version. Returns saved version or None."""
    p = chapter_path(bdir, ch.number)
    text = read_text(p)
    if not text.strip():
        return None
    saved_ver = ch.version
    vpath = chapter_version_path(bdir, ch.number, saved_ver)
    atomic_write_text(vpath, text)
    ch.version = saved_ver + 1
    return saved_ver


def read_ignored_findings(bdir: Path, n: int) -> set[str]:
    data = read_json(bdir / "reports" / "ignored.json", {})
    if not isinstance(data, dict):
        return set()
    return set(data.get(str(n), []))


def add_ignored_finding(bdir: Path, n: int, fingerprint: str) -> set[str]:
    p = bdir / "reports" / "ignored.json"
    data = read_json(p, {})
    if not isinstance(data, dict):
        data = {}
    items = set(data.get(str(n), []))
    items.add(fingerprint)
    data[str(n)] = sorted(items)
    atomic_write_json(p, data)
    return items
