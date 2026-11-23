from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from app import events, jobs
from app.pipeline import canon_gen, characters_gen, voice_gen, world_gen
from app.pipeline._common import load_prompt
from app.schemas import GenerateOpts, JobOut, TextPayload
from app.storage import atomic_write_text, backup_prev, read_text, restore_prev
from app.store import Book, book_dir, book_lock, get_book, save_book

router = APIRouter(prefix="/api/books/{book_id}", tags=["foundation"])

ARTIFACT_MAP: dict[str, str] = {
    "world": "world.md",
    "characters": "characters.md",
    "voice": "voice.md",
    "canon": "canon.md",
    "summary": "summary.md",
    "mystery": "mystery.md",
}

GENERABLE = {"world", "characters", "voice", "canon"}


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


def _require_kind(kind: str) -> str:
    if kind not in ARTIFACT_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown artifact kind '{kind}'")
    return ARTIFACT_MAP[kind]


@router.get("/voice/trials")
def get_voice_trials(book_id: str) -> dict[str, Any]:
    _require_book(book_id)
    txt = read_text(book_dir(book_id) / "voice_trials.md")
    return {"text": txt, "trials": voice_gen.parse_trials(txt)}


@router.post("/voice/trials", response_model=JobOut)
async def post_voice_trials(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    async def _run(job: jobs.Job) -> None:
        bdir = book_dir(book_id)

        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        out = await voice_gen.trials(b, bdir, stream_cb=_cb, on_warning=_warn)
        atomic_write_text(bdir / "voice_trials.md", out)
        events.publish(job.id, {"type": "artifact", "name": "voice_trials.md"})

    j = jobs.enqueue(book_id=book_id, kind="voice_trials", fn=_run)
    return j.to_dict()


ArtifactKind = Literal["world", "characters", "voice", "canon", "summary", "mystery"]


@router.get("/{kind}")
def get_artifact(book_id: str, kind: ArtifactKind) -> dict[str, Any]:
    fname = _require_kind(kind)
    _require_book(book_id)
    bdir = book_dir(book_id)
    p = bdir / fname
    if kind == "voice" and not p.exists():
        default_v = load_prompt("voice_default.md")
        atomic_write_text(p, default_v + "\n")
    prev_p = p.with_name(f"{p.stem}.prev{p.suffix}")
    return {"text": read_text(p), "has_prev": prev_p.exists() and bool(read_text(prev_p).strip())}


@router.put("/{kind}")
async def put_artifact(book_id: str, kind: ArtifactKind, body: TextPayload) -> dict[str, Any]:
    fname = _require_kind(kind)
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        atomic_write_text(bdir / fname, body.text)
        save_book(b)
        prev_p = (bdir / fname).with_name(f"{(bdir / fname).stem}.prev{(bdir / fname).suffix}")
        return {"text": body.text, "has_prev": prev_p.exists() and bool(read_text(prev_p).strip())}


@router.post("/{kind}/restore-prev")
async def restore_artifact_prev(book_id: str, kind: ArtifactKind) -> dict[str, Any]:
    fname = _require_kind(kind)
    async with book_lock(book_id):
        _require_book(book_id)
        p = book_dir(book_id) / fname
        restored = restore_prev(p)
        if restored is None:
            raise HTTPException(status_code=404, detail=f"No previous version saved for {kind}.")
        return {"text": restored, "has_prev": True}


@router.post("/{kind}/generate", response_model=JobOut)
async def generate_artifact(book_id: str, kind: ArtifactKind, body: GenerateOpts | None = None) -> dict[str, Any]:
    if kind not in GENERABLE:
        raise HTTPException(status_code=400, detail=f"'{kind}' cannot be auto-generated directly.")
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    fname = ARTIFACT_MAP[kind]
    choice = body.choice if body else None

    async def _run(job: jobs.Job) -> None:
        bdir = book_dir(book_id)
        ref = read_text(bdir / "uploads" / "_extracted.md")

        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        if kind == "world":
            out = await world_gen.generate(b, bdir, ref_text=ref, stream_cb=_cb, on_warning=_warn)
        elif kind == "characters":
            out = await characters_gen.generate(b, bdir, ref_text=ref, stream_cb=_cb, on_warning=_warn)
        elif kind == "voice":
            out = await voice_gen.generate(b, bdir, choice=choice, stream_cb=_cb, on_warning=_warn)
        else:
            out = await canon_gen.generate(b, bdir, stream_cb=_cb, on_warning=_warn)

        # Backup previous ONLY once new output succeeded
        target = bdir / fname
        backup_prev(target)
        atomic_write_text(target, out)
        async with book_lock(book_id):
            fresh = get_book(book_id)
            if fresh:
                save_book(fresh)
        events.publish(job.id, {"type": "artifact", "name": fname})

    j = jobs.enqueue(book_id=book_id, kind=f"{kind}_generate", fn=_run)
    return j.to_dict()
