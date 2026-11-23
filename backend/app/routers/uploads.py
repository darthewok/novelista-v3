from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from app.storage import atomic_write_bytes, atomic_write_text, read_text, safe_child
from app.store import Book, book_dir, get_book

router = APIRouter(prefix="/api/books/{book_id}", tags=["uploads"])

ALLOWED_DOC_EXT = {".md", ".txt", ".pdf"}
ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_DOC_BYTES = 20 * 1024 * 1024
MAX_IMG_BYTES = 15 * 1024 * 1024
MAX_EXTRACTED_CHARS = 120_000


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = await upload.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"File exceeds {max_bytes // (1024 * 1024)} MB limit.")
        buf.write(chunk)
    return buf.getvalue()


def _extract_doc_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".md", ".txt"}:
        return read_text(path)
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p for p in pages if p.strip())
    return ""


def _rebuild_extracted(udir: Path) -> int:
    chunks: list[str] = []
    for f in sorted(udir.iterdir(), key=lambda p: p.name.lower()):
        if not f.is_file() or f.name.startswith("_") or f.suffix.lower() not in ALLOWED_DOC_EXT:
            continue
        txt = _extract_doc_text(f).strip()
        if txt:
            chunks.append(f"## {f.name}\n\n{txt}")
    joined = "\n\n---\n\n".join(chunks)[:MAX_EXTRACTED_CHARS]
    atomic_write_text(udir / "_extracted.md", joined)
    return len(joined)


@router.get("/uploads")
def list_uploads(book_id: str) -> dict[str, Any]:
    _require_book(book_id)
    udir = book_dir(book_id) / "uploads"
    udir.mkdir(parents=True, exist_ok=True)
    files = [
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(udir.iterdir(), key=lambda p: p.name.lower())
        if f.is_file() and not f.name.startswith("_") and not f.name.startswith("cover.")
    ]
    extracted = udir / "_extracted.md"
    return {
        "files": files,
        "has_extracted": extracted.exists() and bool(read_text(extracted).strip()),
        "has_cover": _find_cover(udir) is not None,
    }


@router.post("/uploads", status_code=201)
async def upload_ref(book_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    _require_book(book_id)
    udir = book_dir(book_id) / "uploads"
    udir.mkdir(parents=True, exist_ok=True)
    raw_name = file.filename or ""
    try:
        dest = safe_child(udir, raw_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if dest.suffix.lower() not in ALLOWED_DOC_EXT:
        raise HTTPException(status_code=400, detail="Only .md, .txt, and .pdf files are supported.")
    if dest.stem.lower() == "cover":
        raise HTTPException(status_code=400, detail="Use the cover upload endpoint for cover images.")

    data = await _read_limited(file, MAX_DOC_BYTES)
    atomic_write_bytes(dest, data)
    try:
        ext_chars = _rebuild_extracted(udir)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not extract text from file: {exc}") from exc

    return {"name": dest.name, "size": len(data), "extracted_chars": ext_chars}


@router.delete("/uploads/{name}", status_code=204)
def delete_ref(book_id: str, name: str) -> Response:
    _require_book(book_id)
    udir = book_dir(book_id) / "uploads"
    try:
        target = safe_child(udir, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    target.unlink()
    _rebuild_extracted(udir)
    return Response(status_code=204)


# --- Cover Image ---

def _find_cover(udir: Path) -> Path | None:
    for ext in ALLOWED_IMG_EXT:
        p = udir / f"cover{ext}"
        if p.exists():
            return p
    return None


@router.get("/cover")
def get_cover(book_id: str) -> Response:
    _require_book(book_id)
    udir = book_dir(book_id) / "uploads"
    p = _find_cover(udir)
    if not p:
        raise HTTPException(status_code=404, detail="No cover uploaded")
    mime = "image/png" if p.suffix.lower() == ".png" else ("image/webp" if p.suffix.lower() == ".webp" else "image/jpeg")
    return Response(content=p.read_bytes(), media_type=mime)


@router.post("/cover", status_code=201)
async def upload_cover(book_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    _require_book(book_id)
    udir = book_dir(book_id) / "uploads"
    udir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "cover.jpg").suffix.lower()
    if ext not in ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail="Cover image must be .jpg, .jpeg, .png, or .webp")
    data = await _read_limited(file, MAX_IMG_BYTES)
    # Remove previous cover format if different
    for old_ext in ALLOWED_IMG_EXT:
        old = udir / f"cover{old_ext}"
        if old.exists():
            old.unlink()
    dest = udir / f"cover{ext}"
    atomic_write_bytes(dest, data)
    return {"name": dest.name, "size": len(data)}


@router.delete("/cover", status_code=204)
def delete_cover(book_id: str) -> Response:
    _require_book(book_id)
    udir = book_dir(book_id) / "uploads"
    for ext in ALLOWED_IMG_EXT:
        p = udir / f"cover{ext}"
        if p.exists():
            p.unlink()
    return Response(status_code=204)


# --- Per-Spread / Per-Chapter Illustrations ---

def _find_illus(idir: Path, n: int) -> Path | None:
    if not idir.exists():
        return None
    for ext in ALLOWED_IMG_EXT:
        p = idir / f"spread_{n:03d}{ext}"
        if p.exists():
            return p
    return None


@router.get("/chapters/{n}/illustration")
def get_illustration(book_id: str, n: int) -> Response:
    _require_book(book_id)
    p = _find_illus(book_dir(book_id) / "uploads" / "illustrations", n)
    if not p:
        raise HTTPException(status_code=404, detail="No illustration for this unit")
    mime = "image/png" if p.suffix.lower() == ".png" else ("image/webp" if p.suffix.lower() == ".webp" else "image/jpeg")
    return Response(content=p.read_bytes(), media_type=mime)


@router.post("/chapters/{n}/illustration", status_code=201)
async def upload_illustration(book_id: str, n: int, file: UploadFile = File(...)) -> dict[str, Any]:
    _require_book(book_id)
    idir = book_dir(book_id) / "uploads" / "illustrations"
    idir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "spread.jpg").suffix.lower()
    if ext not in ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail="Illustration must be .jpg, .jpeg, .png, or .webp")
    data = await _read_limited(file, MAX_IMG_BYTES)
    for old_ext in ALLOWED_IMG_EXT:
        old = idir / f"spread_{n:03d}{old_ext}"
        if old.exists():
            old.unlink()
    dest = idir / f"spread_{n:03d}{ext}"
    atomic_write_bytes(dest, data)
    return {"name": dest.name, "size": len(data)}


@router.delete("/chapters/{n}/illustration", status_code=204)
def delete_illustration(book_id: str, n: int) -> Response:
    _require_book(book_id)
    idir = book_dir(book_id) / "uploads" / "illustrations"
    for ext in ALLOWED_IMG_EXT:
        p = idir / f"spread_{n:03d}{ext}"
        if p.exists():
            p.unlink()
    return Response(status_code=204)
