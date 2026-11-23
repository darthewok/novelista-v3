from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from app import events, jobs
from app.export.docx_export import build_docx_bytes
from app.export.epub_export import build_epub_bytes
from app.export.markdown_export import build_markdown, build_zip_bytes
from app.export.pdf_export import TRIM_PRESETS, build_pdf_bytes
from app.formats import FORMAT_PROFILES, get_profile
from app.pipeline import kdp_kit
from app.schemas import BookCreate, BookOut, BookPatch, FrontMatterSchema, JobOut
from app.storage import atomic_write_text, read_text, safe_slug
from app.store import (
    Book,
    FrontMatter,
    book_dir,
    book_lock,
    chapter_path,
    create_book,
    delete_book,
    get_book,
    list_books,
    save_book,
)

router = APIRouter(prefix="/api/books", tags=["books"])


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


def _book_out(b: Book) -> dict[str, Any]:
    return b.to_dict()


@router.get("/formats")
def get_formats() -> dict[str, Any]:
    return {
        "formats": [
            {
                "key": p.key.value,
                "label": p.label,
                "unit_label": p.unit_label,
                "unit_label_plural": p.unit_label_plural,
                "default_units": p.default_units,
                "min_units": p.min_units,
                "max_units": p.max_units,
                "default_words": p.default_words,
                "min_words": p.min_words,
                "max_words": p.max_words,
                "default_audience": p.default_audience.value,
                "optional_phases": list(p.optional_phases),
                "illustration_support": p.illustration_support,
                "default_trim": p.default_trim,
            }
            for p in FORMAT_PROFILES.values()
        ]
    }


@router.get("", response_model=list[BookOut])
def get_books() -> list[dict[str, Any]]:
    return [_book_out(b) for b in list_books()]


@router.post("", status_code=201, response_model=BookOut)
def post_book(body: BookCreate) -> dict[str, Any]:
    fm = FrontMatter(**body.front_matter.model_dump()) if body.front_matter else FrontMatter()
    b = create_book(
        title=body.title,
        book_format=body.book_format,
        audience=body.audience,
        chapter_count=body.chapter_count,
        target_words=body.target_words,
        pov=body.pov,
        tense=body.tense,
        writing_style=body.writing_style,
        custom_style=body.custom_style,
        genre=body.genre,
        language=body.language,
        idea=body.idea,
        front_matter=fm,
    )
    return _book_out(b)


@router.get("/{book_id}", response_model=BookOut)
def get_single_book(book_id: str) -> dict[str, Any]:
    return _book_out(_require_book(book_id))


@router.patch("/{book_id}", response_model=BookOut)
async def patch_book(book_id: str, body: BookPatch) -> dict[str, Any]:
    async with book_lock(book_id):
        b = _require_book(book_id)
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            if k == "title" and isinstance(v, str):
                v = v.strip() or "Untitled"
            setattr(b, k, v)
        if "idea" in data:
            atomic_write_text(book_dir(book_id) / "seed.txt", b.idea)
        save_book(b)
        return _book_out(b)


@router.delete("/{book_id}", status_code=204)
async def remove_book(book_id: str) -> Response:
    _require_book(book_id)
    await jobs.cancel_book_jobs(book_id)
    delete_book(book_id)
    return Response(status_code=204)


@router.get("/{book_id}/frontmatter", response_model=FrontMatterSchema)
def get_frontmatter(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    return b.to_dict()["front_matter"]


@router.put("/{book_id}/frontmatter", response_model=FrontMatterSchema)
async def put_frontmatter(book_id: str, body: FrontMatterSchema) -> dict[str, Any]:
    async with book_lock(book_id):
        b = _require_book(book_id)
        b.front_matter = FrontMatter(**body.model_dump())
        save_book(b)
        return b.to_dict()["front_matter"]


@router.get("/{book_id}/search")
def search_book(book_id: str, q: str = Query(default="", min_length=1, max_length=120)) -> dict[str, Any]:
    b = _require_book(book_id)
    bdir = book_dir(book_id)
    pat = re.compile(re.escape(q.strip()), re.IGNORECASE)
    hits: list[dict[str, Any]] = []

    def _scan_text(label: str, path_key: str, text: str, chapter: int | None = None) -> None:
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append({"label": label, "target": path_key, "chapter": chapter, "line": i, "snippet": line.strip()[:180]})
                if len(hits) >= 80:
                    return

    for fname, label, key in (
        ("seed.txt", "Seed", "seed"),
        ("world.md", "World", "world"),
        ("characters.md", "Characters", "characters"),
        ("voice.md", "Voice", "voice"),
        ("canon.md", "Canon", "canon"),
        ("summary.md", "Story So Far", "canon"),
        ("outline.md", "Outline", "outline"),
    ):
        if len(hits) >= 80:
            break
        txt = read_text(bdir / fname)
        if txt:
            _scan_text(label, key, txt)

    prof = get_profile(b.book_format)
    for ch in sorted(b.chapters, key=lambda c: c.number):
        if len(hits) >= 80:
            break
        txt = read_text(chapter_path(bdir, ch.number))
        if txt:
            _scan_text(f"{prof.unit_label} {ch.number}: {ch.title}", f"chapter:{ch.number}", txt, ch.number)

    return {"query": q, "count": len(hits), "hits": hits}


@router.get("/{book_id}/kdp-kit")
def get_kdp_kit(book_id: str) -> dict[str, Any]:
    _require_book(book_id)
    return kdp_kit.load_kdp_kit(book_dir(book_id))


@router.put("/{book_id}/kdp-kit")
def put_kdp_kit(book_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _require_book(book_id)
    return kdp_kit.save_kdp_kit(book_dir(book_id), body)


@router.post("/{book_id}/kdp-kit/generate", response_model=JobOut)
async def generate_kdp_kit(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    async def _run(job: jobs.Job) -> None:
        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        kit = await kdp_kit.generate(b, book_dir(book_id), stream_cb=_cb, on_warning=_warn)
        events.publish(job.id, {"type": "artifact", "name": "kdp_kit.json", "data": kit})

    j = jobs.enqueue(book_id=book_id, kind="kdp_kit", fn=_run)
    return j.to_dict()


# --- Publishing Exporters (all pure read-only GETs) ---

@router.get("/{book_id}/manuscript")
def export_markdown(book_id: str, accepted_only: bool = False) -> Response:
    b = _require_book(book_id)
    md = build_markdown(b, book_dir(book_id), accepted_only=accepted_only)
    slug = safe_slug(b.title)
    return Response(
        content=md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}.md"'},
    )


@router.get("/{book_id}/export")
def export_zip(book_id: str, accepted_only: bool = False) -> Response:
    b = _require_book(book_id)
    data = build_zip_bytes(b, book_dir(book_id), accepted_only=accepted_only)
    slug = safe_slug(b.title)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}-project.zip"'},
    )


@router.get("/{book_id}/export/epub")
def export_epub(book_id: str, accepted_only: bool = False) -> Response:
    b = _require_book(book_id)
    data = build_epub_bytes(b, book_dir(book_id), accepted_only=accepted_only)
    slug = safe_slug(b.title)
    return Response(
        content=data,
        media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.epub"'},
    )


@router.get("/{book_id}/export/docx")
def export_docx(book_id: str, accepted_only: bool = False) -> Response:
    b = _require_book(book_id)
    data = build_docx_bytes(b, book_dir(book_id), accepted_only=accepted_only)
    slug = safe_slug(b.title)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{slug}.docx"'},
    )


@router.get("/{book_id}/export/pdf")
def export_pdf(book_id: str, trim: str | None = None, accepted_only: bool = False) -> Response:
    b = _require_book(book_id)
    chosen_trim = trim or get_profile(b.book_format).default_trim
    if chosen_trim not in TRIM_PRESETS:
        raise HTTPException(status_code=422, detail=f"Unsupported trim size '{chosen_trim}'. Choose one of: {', '.join(TRIM_PRESETS)}")
    data = build_pdf_bytes(b, book_dir(book_id), trim=chosen_trim, accepted_only=accepted_only)
    slug = safe_slug(b.title)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{slug}-{chosen_trim}.pdf"'},
    )
