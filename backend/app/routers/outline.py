from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app import events, jobs
from app.enums import ChapterStatus
from app.pipeline import outline_gen
from app.pipeline._common import parse_outline_units
from app.schemas import JobOut, TextPayload
from app.storage import atomic_write_text, backup_prev, read_text
from app.store import Book, ChapterRec, book_dir, book_lock, get_book, save_book

router = APIRouter(prefix="/api/books/{book_id}/outline", tags=["outline"])


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


def sync_chapters(book: Book, outline_md: str) -> int:
    """Sync `book.chapters` from parsed outline headings while preserving drafted/accepted chapters."""
    specs = parse_outline_units(outline_md)
    if not specs:
        return len(book.chapters)

    existing = {c.number: c for c in book.chapters}
    spec_nums = {num for num, _ in specs}
    updated: list[ChapterRec] = []

    for num, title in specs:
        if num in existing:
            rec = existing[num]
            rec.title = title
            updated.append(rec)
        else:
            updated.append(ChapterRec(number=num, title=title))

    # Preserve non-pending chapters even if temporarily missing from outline
    for num, rec in existing.items():
        if num not in spec_nums and rec.status != ChapterStatus.pending:
            updated.append(rec)

    updated.sort(key=lambda c: c.number)
    book.chapters = updated
    if updated:
        book.chapter_count = len(updated)
    return len(updated)


@router.get("")
def get_outline(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    bdir = book_dir(book_id)
    txt = read_text(bdir / "outline.md")
    prev_p = bdir / "outline.prev.md"
    return {
        "text": txt,
        "has_prev": prev_p.exists() and bool(read_text(prev_p).strip()),
        "chapters": [c.to_dict() for c in sorted(b.chapters, key=lambda x: x.number)],
    }


@router.put("")
async def put_outline(book_id: str, body: TextPayload) -> dict[str, Any]:
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        atomic_write_text(bdir / "outline.md", body.text)
        count = sync_chapters(b, body.text)
        save_book(b)
        return {
            "text": body.text,
            "chapter_count": count,
            "chapters": [c.to_dict() for c in sorted(b.chapters, key=lambda x: x.number)],
        }


@router.post("/generate", response_model=JobOut)
async def generate_outline(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")

    async def _run(job: jobs.Job) -> None:
        bdir = book_dir(book_id)
        ref = read_text(bdir / "uploads" / "_extracted.md")

        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        out = await outline_gen.generate(b, bdir, ref_text=ref, stream_cb=_cb, on_warning=_warn)
        parsed = parse_outline_units(out)
        if not parsed:
            _warn("No '### Ch N: Title' or '### Spread N: Title' headings were detected in the outline.")

        target = bdir / "outline.md"
        backup_prev(target)
        atomic_write_text(target, out)
        async with book_lock(book_id):
            fresh = _require_book(book_id)
            sync_chapters(fresh, out)
            save_book(fresh)
        events.publish(job.id, {"type": "artifact", "name": "outline.md"})

    j = jobs.enqueue(book_id=book_id, kind="outline_generate", fn=_run)
    return j.to_dict()
