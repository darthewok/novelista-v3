from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app import events, jobs
from app.enums import ChapterStatus
from app.formats import get_profile
from app.pipeline import (
    adversarial_cut,
    ai_checker,
    canon_gen,
    chapter_draft,
    chapter_revise,
    editor_pass,
    scene_planner,
    slop_fix,
    summary_gen,
)
from app.pipeline._common import is_american_english, pov_name
from app.schemas import (
    ChapterOut,
    ChapterSummaryOut,
    DraftOpts,
    FixFindingOpts,
    IgnoreFindingOpts,
    JobOut,
    ReviseNotes,
    SelectionRewriteOpts,
    TextPayload,
)
from app.settings import load_settings
from app.storage import atomic_write_json, atomic_write_text, read_json, read_text
from app.store import (
    Book,
    ChapterRec,
    add_ignored_finding,
    book_dir,
    book_lock,
    brief_path,
    chapter_path,
    chapter_version_path,
    get_book,
    list_chapter_versions,
    report_path,
    save_book,
    scene_path,
    snapshot_chapter,
)

log = logging.getLogger("novelista.chapters")

router = APIRouter(prefix="/api/books/{book_id}", tags=["chapters"])

EMPTY_REPORT: dict[str, Any] = {
    "error_count": 0,
    "warning_count": 0,
    "tells_total": 0,
    "cleanliness_grade": "",
    "metrics": {},
    "findings": [],
}


def _require_book_and_ch(book_id: str, n: int) -> tuple[Book, ChapterRec]:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    ch = b.get_chapter(n)
    if not ch:
        raise HTTPException(status_code=404, detail=f"Chapter {n} not found")
    return b, ch


def _guard_editable(book_id: str, n: int, ch: ChapterRec) -> None:
    if ch.status == ChapterStatus.accepted:
        raise HTTPException(status_code=409, detail="Chapter is accepted. Un-accept it before editing or regenerating.")
    if jobs.has_active(book_id, n):
        raise HTTPException(status_code=409, detail=f"A job for unit {n} is already active.")


def _apply_report_to_ch(ch: ChapterRec, rep: dict[str, Any]) -> None:
    m = rep.get("metrics") or {}
    ch.word_count = int(m.get("word_count") or ch.word_count)
    ch.error_count = int(rep.get("error_count") or 0)
    ch.warning_count = int(rep.get("warning_count") or 0)
    ch.tells = ch.error_count + ch.warning_count
    ch.cleanliness_grade = str(rep.get("cleanliness_grade") or "")


async def _scan_and_persist(book_id: str, n: int, text: str) -> dict[str, Any]:
    async with book_lock(book_id):
        b = get_book(book_id)
        if not b:
            return EMPTY_REPORT
        bdir = book_dir(book_id)
        rep = ai_checker.scan(text, book=b, bdir=bdir, chapter_num=n)
        atomic_write_json(report_path(bdir, n), rep)
        ch = b.get_chapter(n)
        if ch:
            _apply_report_to_ch(ch, rep)
            save_book(b)
        return rep


async def _commit_new_prose(
    book_id: str,
    n: int,
    new_text: str,
    *,
    new_status: ChapterStatus | None = None,
    snapshot_existing: bool = True,
) -> dict[str, Any]:
    async with book_lock(book_id):
        b = get_book(book_id)
        if not b:
            raise HTTPException(status_code=404, detail="Book not found")
        bdir = book_dir(book_id)
        ch = b.get_chapter(n)
        if not ch:
            raise HTTPException(status_code=404, detail=f"Chapter {n} not found")
        p = chapter_path(bdir, n)
        old_text = read_text(p)
        if snapshot_existing and old_text.strip() and old_text.strip() != new_text.strip():
            snapshot_chapter(bdir, ch)
        atomic_write_text(p, new_text)
        if new_status is not None:
            ch.status = new_status
        rep = ai_checker.scan(new_text, book=b, bdir=bdir, chapter_num=n)
        atomic_write_json(report_path(bdir, n), rep)
        _apply_report_to_ch(ch, rep)
        save_book(b)
        return rep


async def _run_polish(
    job: jobs.Job,
    book_id: str,
    n: int,
    *,
    user_notes: str = "",
) -> tuple[str, dict[str, Any]]:
    settings = load_settings()
    passes = max(1, settings.quality.max_polish_passes)
    tells_thresh = settings.quality.tells_threshold
    bdir = book_dir(book_id)

    async def _tok(tok: str) -> None:
        events.publish(job.id, {"type": "token", "text": tok, "chapter": n})

    def _warn(msg: str) -> None:
        events.publish(job.id, {"type": "warning", "message": msg})

    text = read_text(chapter_path(bdir, n))
    b = get_book(book_id)
    assert b is not None
    norm_start = slop_fix.normalize_prose_typography(text, american_english=is_american_english(b, bdir))
    if norm_start != text:
        text = norm_start
        rep = await _commit_new_prose(book_id, n, text, snapshot_existing=True)
    else:
        rep = ai_checker.scan(text, book=b, bdir=bdir, chapter_num=n)

    for p_idx in range(1, passes + 1):
        b = get_book(book_id)
        assert b is not None
        events.publish(job.id, {"type": "status", "message": f"Polish pass {p_idx}/{passes}: running acquiring editor review..."})
        analysis, brief = await editor_pass.run(
            b, bdir, n, text, scan_report=rep, user_notes=user_notes, on_warning=_warn
        )
        atomic_write_text(brief_path(bdir, n), brief)
        events.publish(job.id, {"type": "artifact", "name": f"briefs/ch_{n:03d}.md"})

        if analysis.get("parse_failed") and rep["error_count"] == 0 and rep["warning_count"] == 0:
            break

        events.publish(job.id, {"type": "status", "message": f"Polish pass {p_idx}/{passes}: revising prose from editorial brief..."})
        revised, no_op = await chapter_revise.generate(
            b, bdir, n, current_text=text, notes=user_notes, brief=brief, stream_cb=_tok, on_warning=_warn
        )
        if not no_op and revised.strip():
            cand_rep = ai_checker.scan(revised, book=b, bdir=bdir, chapter_num=n)
            cand_tells = float((cand_rep.get("metrics") or {}).get("tells_per_1000", 0.0))
            cur_tells = float((rep.get("metrics") or {}).get("tells_per_1000", 0.0))
            regressed_hard = (
                cand_rep["error_count"] > rep["error_count"] + 1
                or (rep["error_count"] == 0 and cand_rep["error_count"] > 0 and cand_tells >= cur_tells)
            )
            if regressed_hard:
                _warn(
                    f"Polish pass {p_idx}: revision introduced hard error regression "
                    f"({cand_rep['error_count']} errors vs {rep['error_count']}); keeping cleaner prior text."
                )
            else:
                text = revised
                rep = await _commit_new_prose(book_id, n, text, new_status=ChapterStatus.revising, snapshot_existing=True)

        # Surgical span fix ONLY for hard mechanical/continuity errors; contextual editor+revise handles style warnings
        if rep["error_count"] > 0:
            events.publish(job.id, {"type": "status", "message": f"Polish pass {p_idx}/{passes}: resolving hard continuity/slop errors..."})
            fixed_err, fix_err_rep = await slop_fix.fix(
                text,
                rep,
                book=b,
                bdir=bdir,
                chapter_num=n,
                include_warnings=False,
                max_rewrites=16,
            )
            events.publish(job.id, {"type": "fix_tells", "report": fix_err_rep})
            if fixed_err.strip() != text.strip():
                text = fixed_err
                rep = await _commit_new_prose(book_id, n, text, snapshot_existing=True)

        tells_per_k = (rep.get("metrics") or {}).get("tells_per_1000", 0.0)
        if rep["error_count"] == 0 and tells_per_k <= tells_thresh:
            break

    if rep["error_count"] > 0:
        events.publish(job.id, {"type": "status", "message": "Running final hard-error pass..."})
        fixed_final, _ = await slop_fix.fix(
            text,
            rep,
            book=b,
            bdir=bdir,
            chapter_num=n,
            include_warnings=False,
            max_rewrites=16,
        )
        if fixed_final.strip() != text.strip():
            text = fixed_final
            rep = await _commit_new_prose(book_id, n, text, snapshot_existing=True)

    norm_end = slop_fix.normalize_prose_typography(text, american_english=is_american_english(b, bdir))
    if norm_end != text:
        text = norm_end
    final_status = ChapterStatus.polished if rep["error_count"] == 0 else ChapterStatus.drafted
    rep = await _commit_new_prose(book_id, n, text, new_status=final_status, snapshot_existing=False)
    return text, rep


async def _update_memory(job: jobs.Job, book_id: str, n: int, text: str) -> None:
    b = get_book(book_id)
    if not b:
        return
    bdir = book_dir(book_id)

    def _warn(msg: str) -> None:
        events.publish(job.id, {"type": "warning", "message": msg})

    events.publish(job.id, {"type": "status", "message": "Updating story summary & continuity ledger..."})
    await summary_gen.update(b, bdir, n, text, on_warning=_warn)
    events.publish(job.id, {"type": "status", "message": "Updating canon facts..."})
    await canon_gen.append_from_chapter(b, bdir, n, text, on_warning=_warn)


# --- Endpoints ---

@router.get("/chapters", response_model=list[ChapterSummaryOut])
def list_chapters(book_id: str) -> list[dict[str, Any]]:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return [c.to_dict() for c in sorted(b.chapters, key=lambda x: x.number)]


@router.get("/chapters/{n}", response_model=ChapterOut)
def get_chapter(book_id: str, n: int) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    bdir = book_dir(book_id)
    d = ch.to_dict()
    d["text"] = read_text(chapter_path(bdir, n))
    return d


@router.put("/chapters/{n}")
async def put_chapter(book_id: str, n: int, body: TextPayload) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    if jobs.has_active(book_id, n):
        raise HTTPException(status_code=409, detail="Wait for active job on this chapter to finish.")
    # Manual edit clears accepted/polished down to drafted if content actually changed
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n))
    next_status = ch.status
    if body.text.strip() != cur.strip():
        if ch.status in (ChapterStatus.pending, ChapterStatus.accepted, ChapterStatus.polished):
            next_status = ChapterStatus.drafted if body.text.strip() else ChapterStatus.pending
    rep = await _commit_new_prose(
        book_id,
        n,
        body.text,
        new_status=next_status,
        snapshot_existing=bool(cur.strip() and body.text.strip() != cur.strip()),
    )
    b2, ch2 = _require_book_and_ch(book_id, n)
    return {"word_count": ch2.word_count, "status": ch2.status.value, "report": rep}


@router.post("/chapters/{n}/scan")
async def scan_chapter_now(book_id: str, n: int) -> dict[str, Any]:
    _, _ = _require_book_and_ch(book_id, n)
    txt = read_text(chapter_path(book_dir(book_id), n))
    return await _scan_and_persist(book_id, n, txt)


@router.get("/chapters/{n}/report")
def get_report(book_id: str, n: int) -> dict[str, Any]:
    _require_book_and_ch(book_id, n)
    data = read_json(report_path(book_dir(book_id), n))
    return data if isinstance(data, dict) else dict(EMPTY_REPORT)


@router.get("/chapters/{n}/brief")
def get_brief(book_id: str, n: int) -> dict[str, str]:
    _require_book_and_ch(book_id, n)
    return {"text": read_text(brief_path(book_dir(book_id), n))}


@router.put("/chapters/{n}/brief")
def put_brief(book_id: str, n: int, body: TextPayload) -> dict[str, str]:
    _require_book_and_ch(book_id, n)
    atomic_write_text(brief_path(book_dir(book_id), n), body.text)
    return {"text": body.text}


@router.get("/chapters/{n}/versions")
def get_versions(book_id: str, n: int) -> dict[str, Any]:
    _, ch = _require_book_and_ch(book_id, n)
    bdir = book_dir(book_id)
    vers = list_chapter_versions(bdir, n)
    return {"current_version": ch.version, "versions": vers}


@router.get("/chapters/{n}/versions/{v}")
def get_version_text(book_id: str, n: int, v: int) -> dict[str, Any]:
    _, ch = _require_book_and_ch(book_id, n)
    bdir = book_dir(book_id)
    if v == ch.version:
        return {"version": v, "text": read_text(chapter_path(bdir, n))}
    vp = chapter_version_path(bdir, n, v)
    if not vp.exists():
        # Check v2 fallback filename
        vp2 = bdir / "chapters" / f"ch_{n:02d}.v{v}.md"
        if not vp2.exists():
            raise HTTPException(status_code=404, detail=f"Version v{v} not found")
        vp = vp2
    return {"version": v, "text": read_text(vp)}


@router.post("/chapters/{n}/versions/{v}/restore")
async def restore_version(book_id: str, n: int, v: int) -> dict[str, Any]:
    _, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    bdir = book_dir(book_id)
    vp = chapter_version_path(bdir, n, v)
    if not vp.exists():
        vp2 = bdir / "chapters" / f"ch_{n:02d}.v{v}.md"
        if not vp2.exists():
            raise HTTPException(status_code=404, detail=f"Version v{v} not found")
        vp = vp2
    restored = read_text(vp)
    rep = await _commit_new_prose(book_id, n, restored, new_status=ChapterStatus.drafted, snapshot_existing=True)
    return {"text": restored, "report": rep}


# --- Scene / Spread Cards ---

@router.get("/chapters/{n}/scenes")
def get_scenes(book_id: str, n: int) -> dict[str, Any]:
    _require_book_and_ch(book_id, n)
    return scene_planner.load_scenes(book_dir(book_id), n)


@router.put("/chapters/{n}/scenes")
async def put_scenes(book_id: str, n: int, body: dict[str, Any]) -> dict[str, Any]:
    async with book_lock(book_id):
        b, ch = _require_book_and_ch(book_id, n)
        saved = scene_planner.save_scenes(book_dir(book_id), n, body)
        scenes = saved.get("scenes") or []
        ch.scene_count = len(scenes)
        ch.has_illustration_prompt = any(bool(s.get("illustration_prompt")) for s in scenes if isinstance(s, dict))
        save_book(b)
        return saved


@router.post("/chapters/{n}/scenes/generate", response_model=JobOut)
async def generate_scenes(book_id: str, n: int) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    if jobs.has_active(book_id, n):
        raise HTTPException(status_code=409, detail=f"A job for unit {n} is already active.")

    async def _run(job: jobs.Job) -> None:
        bdir = book_dir(book_id)

        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok, "chapter": n})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        payload = await scene_planner.generate_scenes(b, bdir, n, stream_cb=_cb, on_warning=_warn)
        async with book_lock(book_id):
            fresh = get_book(book_id)
            if fresh and fresh.get_chapter(n):
                c = fresh.get_chapter(n)
                assert c is not None
                sc = payload.get("scenes") or []
                c.scene_count = len(sc)
                c.has_illustration_prompt = any(bool(s.get("illustration_prompt")) for s in sc if isinstance(s, dict))
                save_book(fresh)
        events.publish(job.id, {"type": "artifact", "name": f"scenes/ch_{n:03d}.json", "data": payload})

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_scenes", chapter=n, fn=_run)
    return j.to_dict()


# --- Accept / Unaccept ---

@router.post("/chapters/{n}/accept")
async def accept_chapter(book_id: str, n: int) -> dict[str, Any]:
    async with book_lock(book_id):
        b, ch = _require_book_and_ch(book_id, n)
        if jobs.has_active(book_id, n):
            raise HTTPException(status_code=409, detail="Active job running on this chapter.")
        txt = read_text(chapter_path(book_dir(book_id), n)).strip()
        if not txt:
            raise HTTPException(status_code=400, detail="Cannot accept an empty chapter.")
        ch.status = ChapterStatus.accepted
        save_book(b)
        return ch.to_dict()


@router.post("/chapters/{n}/unaccept")
async def unaccept_chapter(book_id: str, n: int) -> dict[str, Any]:
    async with book_lock(book_id):
        b, ch = _require_book_and_ch(book_id, n)
        if ch.status != ChapterStatus.accepted:
            raise HTTPException(status_code=409, detail="Chapter is not currently accepted.")
        ch.status = ChapterStatus.drafted
        save_book(b)
        return ch.to_dict()


# --- Drafting, Revising, Polishing, Cutting, Fixing ---

async def _draft_worker(
    job: jobs.Job,
    book_id: str,
    n: int,
    *,
    polish: bool,
    make_scenes: bool,
) -> None:
    bdir = book_dir(book_id)
    b = get_book(book_id)
    if not b:
        return

    async def _tok(tok: str) -> None:
        events.publish(job.id, {"type": "token", "text": tok, "chapter": n})

    def _warn(msg: str) -> None:
        events.publish(job.id, {"type": "warning", "message": msg})

    if make_scenes and not scene_path(bdir, n).exists():
        events.publish(job.id, {"type": "status", "message": f"Planning scenes for unit {n}..."})
        sp = await scene_planner.generate_scenes(b, bdir, n, on_warning=_warn)
        async with book_lock(book_id):
            fresh = get_book(book_id)
            if fresh and fresh.get_chapter(n):
                c = fresh.get_chapter(n)
                assert c is not None
                sc = sp.get("scenes") or []
                c.scene_count = len(sc)
                c.has_illustration_prompt = any(bool(s.get("illustration_prompt")) for s in sc if isinstance(s, dict))
                save_book(fresh)

    events.publish(job.id, {"type": "status", "message": f"Drafting unit {n}..."})
    text, _ = await chapter_draft.generate(b, bdir, n, stream_cb=_tok, on_warning=_warn)
    await _commit_new_prose(book_id, n, text, new_status=ChapterStatus.drafted, snapshot_existing=True)

    if polish:
        text, _ = await _run_polish(job, book_id, n)

    await _update_memory(job, book_id, n, text)
    events.publish(job.id, {"type": "artifact", "name": f"chapters/ch_{n:03d}.md"})


@router.post("/chapters/{n}/draft", response_model=JobOut)
async def draft_chapter_endpoint(book_id: str, n: int, body: DraftOpts | None = None) -> dict[str, Any]:
    _, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    settings = load_settings()
    do_polish = body.polish if (body and body.polish is not None) else settings.quality.auto_polish
    do_scenes = body.scene_cards if (body and body.scene_cards is not None) else settings.quality.auto_scene_cards

    async def _run(job: jobs.Job) -> None:
        await _draft_worker(job, book_id, n, polish=do_polish, make_scenes=do_scenes)

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_draft", chapter=n, fn=_run)
    return j.to_dict()


@router.post("/chapters/{n}/revise", response_model=JobOut)
async def revise_chapter_endpoint(book_id: str, n: int, body: ReviseNotes) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n)).strip()
    if not cur:
        raise HTTPException(status_code=400, detail="Draft the chapter before revising it.")

    async def _run(job: jobs.Job) -> None:
        async def _tok(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok, "chapter": n})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        brief = read_text(brief_path(bdir, n))
        revised, no_op = await chapter_revise.generate(
            b, bdir, n, current_text=cur, notes=body.notes, brief=brief, stream_cb=_tok, on_warning=_warn
        )
        final_text = cur
        if not no_op and revised.strip():
            final_text = revised
            await _commit_new_prose(book_id, n, final_text, new_status=ChapterStatus.drafted, snapshot_existing=True)

        if body.polish:
            final_text, _ = await _run_polish(job, book_id, n, user_notes=body.notes)

        await _update_memory(job, book_id, n, final_text)
        events.publish(job.id, {"type": "artifact", "name": f"chapters/ch_{n:03d}.md"})

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_revise", chapter=n, fn=_run)
    return j.to_dict()


@router.post("/chapters/{n}/polish", response_model=JobOut)
async def polish_chapter_endpoint(book_id: str, n: int) -> dict[str, Any]:
    _, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    if not read_text(chapter_path(book_dir(book_id), n)).strip():
        raise HTTPException(status_code=400, detail="Draft the chapter before polishing.")

    async def _run(job: jobs.Job) -> None:
        final_text, _ = await _run_polish(job, book_id, n)
        await _update_memory(job, book_id, n, final_text)
        events.publish(job.id, {"type": "artifact", "name": f"chapters/ch_{n:03d}.md"})

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_polish", chapter=n, fn=_run)
    return j.to_dict()


@router.post("/chapters/{n}/editor", response_model=JobOut)
async def editor_chapter_endpoint(book_id: str, n: int) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    if jobs.has_active(book_id, n):
        raise HTTPException(status_code=409, detail=f"A job for unit {n} is already active.")
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n)).strip()
    if not cur:
        raise HTTPException(status_code=400, detail="Draft the chapter before requesting editor notes.")

    async def _run(job: jobs.Job) -> None:
        async def _tok(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok, "chapter": n})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        rep = await _scan_and_persist(book_id, n, cur)
        _, brief = await editor_pass.run(b, bdir, n, cur, scan_report=rep, stream_cb=_tok, on_warning=_warn)
        atomic_write_text(brief_path(bdir, n), brief)
        events.publish(job.id, {"type": "artifact", "name": f"briefs/ch_{n:03d}.md"})

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_editor", chapter=n, fn=_run)
    return j.to_dict()


@router.post("/chapters/{n}/tighten", response_model=JobOut)
async def tighten_chapter_endpoint(book_id: str, n: int) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n)).strip()
    if not cur:
        raise HTTPException(status_code=400, detail="Draft the chapter before tightening.")

    async def _run(job: jobs.Job) -> None:
        async def _tok(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok, "chapter": n})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        cut_text, cut_rep = await adversarial_cut.run(b, bdir, n, cur, percent=10, stream_cb=_tok, on_warning=_warn)
        events.publish(job.id, {"type": "tighten", "report": cut_rep})
        if cut_rep.get("applied_cuts", 0) > 0 and cut_text.strip() != cur.strip():
            await _commit_new_prose(book_id, n, cut_text, snapshot_existing=True)
        events.publish(job.id, {"type": "artifact", "name": f"chapters/ch_{n:03d}.md"})

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_tighten", chapter=n, fn=_run)
    return j.to_dict()


@router.post("/chapters/{n}/fix-tells", response_model=JobOut)
async def fix_tells_endpoint(book_id: str, n: int) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n)).strip()
    if not cur:
        raise HTTPException(status_code=400, detail="Draft the chapter before fixing tells.")

    async def _run(job: jobs.Job) -> None:
        rep = await _scan_and_persist(book_id, n, cur)
        fixed, fix_rep = await slop_fix.fix(cur, rep, book=b, bdir=bdir, chapter_num=n, include_warnings=True)
        events.publish(job.id, {"type": "fix_tells", "report": fix_rep})
        if fixed.strip() != cur.strip():
            await _commit_new_prose(book_id, n, fixed, snapshot_existing=True)
        events.publish(job.id, {"type": "artifact", "name": f"chapters/ch_{n:03d}.md"})

    j = jobs.enqueue(book_id=book_id, kind=f"chapter_{n}_fix_tells", chapter=n, fn=_run)
    return j.to_dict()


@router.post("/chapters/{n}/fix-finding")
async def fix_single_finding(book_id: str, n: int, body: FixFindingOpts) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n))
    rep = ai_checker.scan(cur, book=b, bdir=bdir, chapter_num=n)

    target = next(
        (
            f
            for f in rep["findings"]
            if (f.get("fingerprint") == body.finding_id)
            or (body.start_char is not None and f.get("start_char") == body.start_char and f.get("rule") == body.finding_id.split(":")[0])
        ),
        None,
    )
    if not target:
        raise HTTPException(status_code=404, detail="Finding no longer matches current text; re-scan first.")
    s0 = int(target.get("start_char") or 0)
    s1 = int(target.get("end_char") or 0)
    if s1 <= s0:
        raise HTTPException(status_code=400, detail="This finding is a chapter-level metric and has no single sentence span.")

    updated, changed = await slop_fix.fix_one(
        cur,
        start_char=s0,
        end_char=s1,
        message=str(target.get("message") or ""),
        rule=str(target.get("rule") or ""),
        quote=str(target.get("quote") or ""),
        book=b,
        bdir=bdir,
    )
    if not changed:
        raise HTTPException(status_code=422, detail="Could not rewrite this line differently.")
    new_rep = await _commit_new_prose(book_id, n, updated, snapshot_existing=True)
    return {"text": updated, "report": new_rep}


@router.post("/chapters/{n}/ignore-finding")
async def ignore_single_finding(book_id: str, n: int, body: IgnoreFindingOpts) -> dict[str, Any]:
    _require_book_and_ch(book_id, n)
    bdir = book_dir(book_id)
    add_ignored_finding(bdir, n, body.fingerprint)
    cur = read_text(chapter_path(bdir, n))
    rep = await _scan_and_persist(book_id, n, cur)
    return rep


@router.post("/chapters/{n}/rewrite-selection")
async def rewrite_selection_endpoint(book_id: str, n: int, body: SelectionRewriteOpts) -> dict[str, Any]:
    b, ch = _require_book_and_ch(book_id, n)
    _guard_editable(book_id, n, ch)
    bdir = book_dir(book_id)
    cur = read_text(chapter_path(bdir, n))
    if body.end_char > len(cur) or body.start_char >= body.end_char:
        raise HTTPException(status_code=422, detail="Invalid character range for current chapter text.")
    replacement = await chapter_revise.rewrite_selection(
        b,
        bdir,
        full_text=cur,
        start_char=body.start_char,
        end_char=body.end_char,
        action=body.action,
        instruction=body.instruction,
    )
    return {
        "start_char": body.start_char,
        "end_char": body.end_char,
        "original": cur[body.start_char : body.end_char],
        "replacement": replacement,
    }


# --- Batch Draft All Pending ---

@router.post("/draft-all")
async def draft_all_pending(book_id: str, body: DraftOpts | None = None) -> dict[str, Any]:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    pending = [c.number for c in sorted(b.chapters, key=lambda x: x.number) if c.status == ChapterStatus.pending]
    if not pending:
        raise HTTPException(status_code=400, detail="No pending chapters to draft.")

    settings = load_settings()
    do_polish = body.polish if (body and body.polish is not None) else settings.quality.auto_polish
    do_scenes = body.scene_cards if (body and body.scene_cards is not None) else settings.quality.auto_scene_cards

    batch = jobs.create_batch(book_id, pending)
    job_ids: list[str] = []
    for num in pending:
        async def _run(job: jobs.Job, ch_num: int = num) -> None:
            await _draft_worker(job, book_id, ch_num, polish=do_polish, make_scenes=do_scenes)

        j = jobs.enqueue(book_id=book_id, kind=f"chapter_{num}_draft", chapter=num, batch_id=batch.id, fn=_run)
        job_ids.append(j.id)

    return {"batch_id": batch.id, "chapters": pending, "job_ids": job_ids}


@router.post("/draft-all/{batch_id}/pause")
def pause_batch_endpoint(book_id: str, batch_id: str) -> dict[str, bool]:
    return {"paused": jobs.pause_batch(batch_id)}


@router.post("/draft-all/{batch_id}/cancel")
def cancel_batch_endpoint(book_id: str, batch_id: str) -> dict[str, int]:
    return {"cancelled": jobs.cancel_batch(batch_id)}
