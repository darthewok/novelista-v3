from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from app import events, jobs
from app.pipeline import seed_gen
from app.pipeline._common import read_reference_material
from app.schemas import JobOut, SeedChoose, TextPayload
from app.storage import atomic_write_text, read_text
from app.store import Book, book_dir, book_lock, get_book, save_book

router = APIRouter(prefix="/api/books/{book_id}/seed", tags=["seed"])

_CAND_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s*|\*\*)?(\d{1,2})\.\s*(?:\*\*)?([^\n*]+?)(?:\*\*)?(?:\s*[—\-:]\s*(.*))?$",
    re.MULTILINE,
)


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


def parse_candidates(md_text: str) -> list[dict[str, Any]]:
    raw = (md_text or "").strip()
    if not raw:
        return []
    matches = list(_CAND_HEADING_RE.finditer(raw))
    seen: set[int] = set()
    items: list[tuple[int, str, int, int]] = []
    for m in matches:
        idx = int(m.group(1))
        if idx < 1 or idx > 20 or idx in seen:
            continue
        seen.add(idx)
        title = m.group(2).strip()
        items.append((idx, title, m.start(), m.end()))
    out: list[dict[str, Any]] = []
    for pos, (idx, title, s0, _) in enumerate(items):
        end = items[pos + 1][2] if pos + 1 < len(items) else len(raw)
        block = raw[s0:end].strip()
        # Strip leading "N. " prefix for clean body display
        clean_body = re.sub(r"^\s*(?:#{1,4}\s*)?\d{1,2}\.\s*", "", block).strip()
        out.append({"index": idx, "title": title, "text": clean_body})
    return out


@router.get("")
def get_seed(book_id: str) -> dict[str, str]:
    b = _require_book(book_id)
    bdir = book_dir(book_id)
    txt = read_text(bdir / "seed.txt").strip() or b.idea
    return {"text": txt}


@router.put("")
async def put_seed(book_id: str, body: TextPayload) -> dict[str, str]:
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        clean = body.text.strip()
        atomic_write_text(bdir / "seed.txt", clean)
        b.idea = clean
        save_book(b)
        return {"text": clean}


@router.get("/candidates")
def get_candidates(book_id: str) -> dict[str, Any]:
    _require_book(book_id)
    raw = read_text(book_dir(book_id) / "seed_candidates.md")
    return {"text": raw, "candidates": parse_candidates(raw)}


@router.post("/candidates", response_model=JobOut)
async def post_candidates(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    async def _run(job: jobs.Job) -> None:
        bdir = book_dir(book_id)
        ref = read_reference_material(bdir)

        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        out = await seed_gen.generate_candidates(b, count=10, ref_text=ref, stream_cb=_cb, on_warning=_warn)
        atomic_write_text(bdir / "seed_candidates.md", out)
        events.publish(job.id, {"type": "artifact", "name": "seed_candidates.md"})

    j = jobs.enqueue(book_id=book_id, kind="seed_candidates", fn=_run)
    return j.to_dict()


@router.post("/choose")
async def choose_seed(book_id: str, body: SeedChoose) -> dict[str, str]:
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        chosen = ""
        if body.text is not None and body.text.strip():
            chosen = body.text.strip()
        elif body.index is not None:
            cands = parse_candidates(read_text(bdir / "seed_candidates.md"))
            hit = next((c for c in cands if c["index"] == body.index), None)
            if not hit:
                raise HTTPException(status_code=404, detail=f"Candidate #{body.index} not found.")
            chosen = hit["text"]
        else:
            raise HTTPException(status_code=422, detail="Provide either `text` or `index`.")

        atomic_write_text(bdir / "seed.txt", chosen)
        b.idea = chosen
        save_book(b)
        return {"text": chosen}
