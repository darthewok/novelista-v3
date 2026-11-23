from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from app.enums import ChapterStatus
from app.formats import get_profile
from app.storage import read_text
from app.store import Book, ChapterRec, chapter_path

_INLINE_FMT_RE = re.compile(r"(\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_)")
_LEADING_CH_HEADING_RE = re.compile(r"^\s*#{1,4}\s*(?:Ch(?:apter)?|Spread|Part)\.?\s*\d+[^\n]*\n+", re.IGNORECASE)


@dataclass
class ExportChapter:
    number: int
    title: str
    body_md: str
    paragraphs: list[str]
    illustration_path: Path | None = None


def collect_chapters(book: Book, bdir: Path, *, accepted_only: bool = False) -> list[ExportChapter]:
    out: list[ExportChapter] = []
    for ch in sorted(book.chapters, key=lambda c: c.number):
        if accepted_only and ch.status != ChapterStatus.accepted:
            continue
        raw = read_text(chapter_path(bdir, ch.number)).strip()
        if not raw:
            continue
        # Strip duplicate leading chapter heading if drafter included one
        cleaned = _LEADING_CH_HEADING_RE.sub("", raw, count=1).strip()
        paras = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
        illus = find_chapter_illustration(bdir, ch.number)
        out.append(
            ExportChapter(
                number=ch.number,
                title=ch.title,
                body_md=cleaned,
                paragraphs=paras,
                illustration_path=illus,
            )
        )
    return out


def find_cover_image(bdir: Path) -> Path | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = bdir / "uploads" / f"cover.{ext}"
        if p.exists():
            return p
    return None


def find_chapter_illustration(bdir: Path, n: int) -> Path | None:
    idir = bdir / "uploads" / "illustrations"
    if not idir.exists():
        return None
    for ext in ("jpg", "jpeg", "png", "webp"):
        for stem in (f"spread_{n:03d}", f"spread_{n:02d}", f"ch_{n:03d}", f"ch_{n:02d}"):
            p = idir / f"{stem}.{ext}"
            if p.exists():
                return p
    return None


def is_scene_break(para: str) -> bool:
    s = para.strip()
    return s in {"---", "***", "* * *", "*  *  *", "___", "#"}


def md_inline_to_html(text: str) -> str:
    """Convert `*italic*` and `**bold**` safely into XHTML while escaping raw HTML chars."""
    out: list[str] = []
    pos = 0
    for m in _INLINE_FMT_RE.finditer(text):
        if m.start() > pos:
            out.append(html.escape(text[pos : m.start()]))
        tok = m.group(0)
        if tok.startswith("**") and tok.endswith("**"):
            out.append(f"<strong>{html.escape(tok[2:-2])}</strong>")
        else:
            out.append(f"<em>{html.escape(tok[1:-1])}</em>")
        pos = m.end()
    if pos < len(text):
        out.append(html.escape(text[pos:]))
    return "".join(out)


def parse_inline_runs(text: str) -> list[tuple[str, bool, bool]]:
    """Return list of (segment, bold, italic) tuples for DOCX/PDF rendering."""
    runs: list[tuple[str, bool, bool]] = []
    pos = 0
    for m in _INLINE_FMT_RE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos : m.start()], False, False))
        tok = m.group(0)
        if tok.startswith("**") and tok.endswith("**"):
            runs.append((tok[2:-2], True, False))
        else:
            runs.append((tok[1:-1], False, True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False, False))
    return runs or [(text, False, False)]


def unit_heading_text(book: Book, ch: ExportChapter) -> str:
    prof = get_profile(book.book_format)
    if prof.key.value == "picture_book":
        return f"Page {ch.number}" if ch.title.lower().startswith("spread") else ch.title
    return f"{prof.unit_label} {ch.number}: {ch.title}"
