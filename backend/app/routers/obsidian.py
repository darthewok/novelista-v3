from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app import events, jobs, obsidian
from app.routers.outline import sync_chapters
from app.schemas import (
    JobOut,
    ObsidianBrowseRequest,
    ObsidianCreateBookRequest,
    ObsidianLinkUpdateRequest,
    ObsidianPushRequest,
    ObsidianScanRequest,
    ObsidianSyncRequest,
)
from app.storage import atomic_write_text, backup_prev
from app.store import (
    Book,
    FrontMatter,
    book_dir,
    book_lock,
    create_book,
    delete_book,
    get_book,
    save_book,
)

router = APIRouter(tags=["obsidian"])


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


@router.post("/api/obsidian/browse")
def browse_local_folders(body: ObsidianBrowseRequest) -> dict[str, Any]:
    try:
        return obsidian.browse_directories(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/obsidian/scan")
def scan_local_vault(body: ObsidianScanRequest) -> dict[str, Any]:
    try:
        return obsidian.scan_vault(body.vault_path, write_back_dir=body.write_back_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/obsidian/suggest-mapping")
async def suggest_vault_mapping(body: ObsidianScanRequest) -> dict[str, Any]:
    try:
        scan_data = obsidian.scan_vault(body.vault_path, write_back_dir=body.write_back_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return await obsidian.suggest_mapping_with_llm(scan_data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"AI mapping failed: {exc}") from exc


@router.post("/api/obsidian/books", status_code=201)
async def create_obsidian_book(body: ObsidianCreateBookRequest) -> dict[str, Any]:
    try:
        cfg = obsidian.normalize_mapping_config(
            {
                "vault_path": body.vault_path,
                "folder_rules": body.folder_rules,
                "file_overrides": body.file_overrides,
                "tag_rules": body.tag_rules,
                "write_back_dir": body.write_back_dir,
                "auto_sync_before_draft": body.auto_sync_before_draft,
                "import_chapters": body.import_chapters,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fm = FrontMatter(**body.front_matter.model_dump()) if body.front_matter else FrontMatter()
    book = create_book(
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
        source_mode="obsidian",
        obsidian_vault_path=cfg["vault_path"],
        front_matter=fm,
    )
    try:
        async with book_lock(book.id):
            bdir = book_dir(book.id)
            sync_report = obsidian.compile_vault_to_book(
                book,
                bdir,
                cfg,
                import_chapters=body.import_chapters,
                backup=False,
            )
            fresh = get_book(book.id) or book
            return {"book": fresh.to_dict(), "sync": sync_report}
    except Exception as exc:
        delete_book(book.id)
        raise HTTPException(status_code=422, detail=f"Failed to compile Obsidian vault: {exc}") from exc


@router.get("/api/books/{book_id}/obsidian")
def get_book_obsidian_status(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    return obsidian.get_vault_status(b, book_dir(book_id))


@router.put("/api/books/{book_id}/obsidian")
async def link_or_update_obsidian(book_id: str, body: ObsidianLinkUpdateRequest) -> dict[str, Any]:
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")
    try:
        cfg = obsidian.normalize_mapping_config(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        prev = obsidian.load_obsidian_config(bdir) or {}
        if body.sync_now:
            sync_report = obsidian.compile_vault_to_book(
                b,
                bdir,
                cfg,
                import_chapters=body.import_chapters,
                backup=True,
            )
        else:
            b.source_mode = "obsidian"
            b.obsidian_vault_path = cfg["vault_path"]
            save_book(b)
            merged = {
                **cfg,
                "last_synced_at": prev.get("last_synced_at"),
                "needs_outline_structuring": prev.get("needs_outline_structuring", False),
                "note_hashes": prev.get("note_hashes") or {},
                "note_roles": prev.get("note_roles") or {},
            }
            obsidian.save_obsidian_config(bdir, merged)
            sync_report = None
        fresh = get_book(book_id) or b
        status = obsidian.get_vault_status(fresh, bdir)
        return {"book": fresh.to_dict(), "sync": sync_report, "status": status}


@router.delete("/api/books/{book_id}/obsidian")
async def unlink_book_obsidian(book_id: str) -> dict[str, Any]:
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        cfg_p = obsidian.obsidian_config_path(bdir)
        if cfg_p.exists():
            cfg_p.unlink()
        b.source_mode = "wizard"
        b.obsidian_vault_path = ""
        save_book(b)
        return {"book": b.to_dict(), "unlinked": True}


@router.post("/api/books/{book_id}/obsidian/sync")
async def sync_book_obsidian(book_id: str, body: ObsidianSyncRequest | None = None) -> dict[str, Any]:
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        cfg = obsidian.load_obsidian_config(bdir)
        if not cfg or not cfg.get("vault_path"):
            raise HTTPException(status_code=400, detail="This book is not linked to an Obsidian vault.")
        try:
            sync_report = obsidian.compile_vault_to_book(
                b,
                bdir,
                cfg,
                roles=body.roles if body else None,
                import_chapters=body.import_chapters if body else None,
                backup=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        fresh = get_book(book_id) or b
        status = obsidian.get_vault_status(fresh, bdir)
        return {"book": fresh.to_dict(), "sync": sync_report, "status": status}


@router.post("/api/books/{book_id}/obsidian/suggest-mapping")
async def suggest_book_obsidian_mapping(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    bdir = book_dir(book_id)
    cfg = obsidian.load_obsidian_config(bdir)
    if not cfg or not cfg.get("vault_path"):
        raise HTTPException(status_code=400, detail="This book is not linked to an Obsidian vault.")
    try:
        scan_data = obsidian.scan_vault(str(cfg["vault_path"]), write_back_dir=str(cfg.get("write_back_dir") or "Novelista Export"))
        return await obsidian.suggest_mapping_with_llm(scan_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"AI mapping failed: {exc}") from exc


@router.post("/api/books/{book_id}/obsidian/structure-outline", response_model=JobOut)
async def structure_vault_outline(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")

    async def _run(job: jobs.Job) -> None:
        bdir = book_dir(book_id)

        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        out = await obsidian.structure_outline_from_vault(b, bdir, stream_cb=_cb, on_warning=_warn)
        target = bdir / "outline.md"
        backup_prev(target)
        atomic_write_text(target, out)
        async with book_lock(book_id):
            fresh = _require_book(book_id)
            sync_chapters(fresh, out)
            save_book(fresh)
            cfg = obsidian.load_obsidian_config(bdir)
            if cfg:
                cfg["needs_outline_structuring"] = False
                cfg["structured_outline_source_sig"] = obsidian.outline_source_signature_for_config(cfg)
                obsidian.save_obsidian_config(bdir, cfg)
        events.publish(job.id, {"type": "artifact", "name": "outline.md"})

    j = jobs.enqueue(book_id=book_id, kind="obsidian_structure_outline", fn=_run)
    return j.to_dict()


@router.post("/api/books/{book_id}/obsidian/push")
async def push_book_obsidian(book_id: str, body: ObsidianPushRequest | None = None) -> dict[str, Any]:
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is active for this book.")
    req = body or ObsidianPushRequest()
    async with book_lock(book_id):
        b = _require_book(book_id)
        bdir = book_dir(book_id)
        try:
            return obsidian.push_book_to_vault(
                b,
                bdir,
                write_back_dir=req.write_back_dir,
                include_manuscript=req.include_manuscript,
                include_chapters=req.include_chapters,
                include_summary=req.include_summary,
                include_canon=req.include_canon,
                accepted_only=req.accepted_only,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
