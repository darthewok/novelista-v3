from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.export.helpers import collect_chapters, unit_heading_text
from app.storage import read_text
from app.store import Book


def build_markdown(book: Book, bdir: Path, *, accepted_only: bool = False) -> str:
    """Pure read-only Markdown assembly (never mutates disk on GET)."""
    chapters = collect_chapters(book, bdir, accepted_only=accepted_only)
    fm = book.front_matter
    blocks: list[str] = [f"# {book.title}"]
    if fm.subtitle.strip():
        blocks.append(f"*{fm.subtitle.strip()}*")
    if fm.author_name.strip():
        blocks.append(f"By {fm.author_name.strip()}")
    if fm.dedication.strip():
        blocks.append(f"## Dedication\n\n_{fm.dedication.strip()}_")

    for ch in chapters:
        blocks.append(f"## {unit_heading_text(book, ch)}\n\n{ch.body_md}")

    if fm.author_bio.strip():
        blocks.append(f"## About the Author\n\n{fm.author_bio.strip()}")
    return "\n\n---\n\n".join(blocks) + "\n"


def build_zip_bytes(book: Book, bdir: Path, *, accepted_only: bool = False) -> bytes:
    """Assemble full project archive cleanly in-memory without mutating book directory."""
    buf = io.BytesIO()
    manuscript = build_markdown(book, bdir, accepted_only=accepted_only)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manuscript.md", manuscript)
        for fname in (
            "book.json",
            "seed.txt",
            "world.md",
            "characters.md",
            "voice.md",
            "canon.md",
            "outline.md",
            "summary.md",
            "continuity.json",
            "mystery.md",
            "kdp_kit.md",
            "kdp_kit.json",
        ):
            p = bdir / fname
            if p.exists():
                zf.writestr(fname, p.read_bytes())

        for ch in collect_chapters(book, bdir, accepted_only=accepted_only):
            zf.writestr(f"chapters/ch_{ch.number:03d}.md", ch.body_md)
            if ch.illustration_path and ch.illustration_path.exists():
                zf.writestr(f"illustrations/{ch.illustration_path.name}", ch.illustration_path.read_bytes())
    return buf.getvalue()
