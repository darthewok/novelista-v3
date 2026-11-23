from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app import events, jobs
from app.enums import ChapterStatus
from app.formats import get_profile
from app.pipeline import chapter_revise, manuscript_audit, slop_fix
from app.pipeline._common import is_american_english
from app.pipeline.directives import per_unit_word_target
from app.routers.chapters import _commit_new_prose, _update_memory
from app.schemas import AuditFixOpts, JobOut
from app.storage import read_text
from app.store import Book, book_dir, chapter_path, get_book

router = APIRouter(prefix="/api/books/{book_id}/audit", tags=["audit"])

_UNIT_SPAN_RE = re.compile(
    r"\b(?:Chapters?|Chs?\.?|Parts?|Spreads?|Units?)\s+"
    r"([0-9]+(?:\s*(?:,\s*(?:and|&)\b|[-–—]|\bto\b|,|&|\band\b)\s*(?:Chapters?|Chs?\.?|Parts?|Spreads?|Units?)?\s*[0-9]+)*)",
    re.IGNORECASE,
)


def _extract_unit_numbers_from_issue(text: str, valid_nums: set[int]) -> set[int]:
    """Return unit numbers explicitly referenced in an audit issue, or empty set if manuscript-wide."""
    found: set[int] = set()
    for m in _UNIT_SPAN_RE.finditer(text or ""):
        seg = m.group(1)
        for rm in re.finditer(r"(\d+)\s*(?:[-–—]|\bto\b)\s*(\d+)", seg, re.IGNORECASE):
            lo, hi = int(rm.group(1)), int(rm.group(2))
            if 1 <= lo <= hi <= lo + 25:
                for num in range(lo, hi + 1):
                    if num in valid_nums:
                        found.add(num)
        for dm in re.finditer(r"\b(\d+)\b", seg):
            num = int(dm.group(1))
            if num in valid_nums:
                found.add(num)
    return found


def plan_audit_fix_targets(
    book: Book,
    bdir: Path,
    global_issues: list[str],
    priority_fixes: list[tuple[int, str]],
) -> tuple[dict[int, dict[str, list[str]]], list[int]]:
    """Map selected global + per-unit audit issues to editable drafted chapters (`{n: {'priority': [...], 'global': [...]}}`)."""
    editable_nums: set[int] = set()
    accepted_nums: set[int] = set()
    for ch in book.chapters:
        if not read_text(chapter_path(bdir, ch.number)).strip():
            continue
        if ch.status == ChapterStatus.accepted:
            accepted_nums.add(ch.number)
        else:
            editable_nums.add(ch.number)

    all_drafted = editable_nums | accepted_nums
    plan: dict[int, dict[str, list[str]]] = {}
    skipped_accepted: set[int] = set()

    def _add(n: int, bucket: str, item: str) -> None:
        clean = item.strip()
        if not clean:
            return
        entry = plan.setdefault(n, {"priority": [], "global": []})
        if clean not in entry[bucket]:
            entry[bucket].append(clean)

    for ch_num, action in priority_fixes:
        if ch_num in editable_nums:
            _add(ch_num, "priority", action)
        elif ch_num in accepted_nums:
            skipped_accepted.add(ch_num)

    for issue in global_issues:
        clean_iss = str(issue or "").strip()
        if not clean_iss:
            continue
        mentioned = _extract_unit_numbers_from_issue(clean_iss, all_drafted)
        targets = mentioned if mentioned else all_drafted
        for n in sorted(targets):
            if n in editable_nums:
                _add(n, "global", clean_iss)
            elif n in accepted_nums:
                skipped_accepted.add(n)

    return plan, sorted(skipped_accepted)


def _require_book(book_id: str) -> Book:
    b = get_book(book_id)
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    return b


@router.get("")
def get_audit(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    bdir = book_dir(book_id)
    saved = manuscript_audit.load_audit(bdir)
    det = manuscript_audit.compute_deterministic_audit(b, bdir)
    if saved:
        saved["deterministic"] = det
        return saved
    return {"deterministic": det, "editorial": None}


@router.post("/run", response_model=JobOut)
async def run_audit(book_id: str) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    async def _run(job: jobs.Job) -> None:
        async def _cb(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        res = await manuscript_audit.run_full_audit(b, book_dir(book_id), stream_cb=_cb, on_warning=_warn)
        events.publish(job.id, {"type": "artifact", "name": "reports/manuscript_audit.json", "data": res})

    j = jobs.enqueue(book_id=book_id, kind="manuscript_audit", fn=_run)
    return j.to_dict()


@router.post("/fix", response_model=JobOut)
async def fix_audit_issues(book_id: str, body: AuditFixOpts) -> dict[str, Any]:
    b = _require_book(book_id)
    if jobs.has_active(book_id):
        raise HTTPException(status_code=409, detail="Another job is already active for this book.")

    bdir = book_dir(book_id)
    if body.fix_all:
        saved = manuscript_audit.load_audit(bdir) or {}
        ed = saved.get("editorial") if isinstance(saved, dict) else None
        if not isinstance(ed, dict):
            raise HTTPException(status_code=400, detail="Run a manuscript audit before applying Fix All.")
        global_issues = [
            str(s).strip()
            for s in [
                *(ed.get("structural_issues") or []),
                *(ed.get("continuity_risks") or []),
                *(ed.get("ai_style_patterns") or []),
            ]
            if str(s).strip()
        ]
        priority_fixes = [
            (int(tf["chapter"]), str(tf.get("action") or "").strip())
            for tf in (ed.get("top_fixes") or [])
            if isinstance(tf, dict) and tf.get("chapter") is not None and str(tf.get("action") or "").strip()
        ]
    else:
        global_issues = [str(s).strip() for s in body.global_issues if str(s).strip()]
        priority_fixes = [(p.chapter, p.action.strip()) for p in body.priority_fixes if p.action.strip()]

    if not global_issues and not priority_fixes:
        raise HTTPException(status_code=400, detail="Select at least one audit issue to fix.")

    plan, skipped_accepted = plan_audit_fix_targets(b, bdir, global_issues, priority_fixes)
    if not plan:
        if skipped_accepted:
            prof = get_profile(b.book_format)
            locked_str = ", ".join(f"{prof.unit_label} {n}" for n in skipped_accepted)
            raise HTTPException(
                status_code=400,
                detail=f"Matching unit(s) are accepted/locked ({locked_str}). Un-accept them before running auto-fix.",
            )
        raise HTTPException(status_code=400, detail="No drafted units matched the selected audit issues.")

    async def _run(job: jobs.Job) -> None:
        prof = get_profile(b.book_format)

        async def _tok(tok: str) -> None:
            events.publish(job.id, {"type": "token", "text": tok})

        def _warn(msg: str) -> None:
            events.publish(job.id, {"type": "warning", "message": msg})

        for acc_n in skipped_accepted:
            _warn(f"Skipped {prof.unit_label} {acc_n} (accepted/locked — unaccept it to include in auto-fix).")

        target_nums = sorted(plan.keys())
        for idx, n in enumerate(target_nums, 1):
            if job._cancel_requested:
                return
            fresh_b = get_book(book_id)
            if not fresh_b:
                return
            cur = read_text(chapter_path(bdir, n)).strip()
            if not cur:
                continue

            cur_words = len(cur.split())
            per_unit = per_unit_word_target(fresh_b)
            unit_items = plan[n]

            note_lines: list[str] = [f"Fix these editorial audit issues for {prof.unit_label} {n}:"]
            for p_act in unit_items["priority"]:
                note_lines.append(f"- [Priority {prof.unit_label} {n} Fix] {p_act}")
            for g_iss in unit_items["global"]:
                note_lines.append(f"- [Manuscript Issue] {g_iss}")
            if per_unit and cur_words < per_unit and any(
                re.search(r"\b(?:word count|below the minimum|threshold|descriptive density|expand)\b", g, re.IGNORECASE)
                for g in [*unit_items["priority"], *unit_items["global"]]
            ):
                note_lines.append(
                    f"- [Word Count Expansion] Expand thin scenes and live spoken dialogue exchanges toward ~{per_unit:,} words."
                )

            audit_notes = "\n".join(note_lines)
            events.publish(
                job.id,
                {"type": "status", "message": f"Fixing {prof.unit_label} {n} ({idx}/{len(target_nums)}): revising prose..."},
            )
            revised, no_op = await chapter_revise.generate(
                fresh_b,
                bdir,
                n,
                current_text=cur,
                notes=audit_notes,
                brief="",
                stream_cb=_tok,
                on_warning=_warn,
            )
            if no_op and ("—" in cur or "–" in cur or "--" in cur):
                cleaned_cur = slop_fix.normalize_prose_typography(
                    cur,
                    american_english=is_american_english(fresh_b, bdir),
                )
                if cleaned_cur.strip() and cleaned_cur.strip() != cur.strip():
                    revised = cleaned_cur
                    no_op = False
            if not no_op and revised.strip():
                final_text = revised
                rep = await _commit_new_prose(
                    book_id,
                    n,
                    final_text,
                    new_status=ChapterStatus.drafted,
                    snapshot_existing=True,
                )
                if rep["error_count"] > 0:
                    events.publish(
                        job.id,
                        {"type": "status", "message": f"Fixing {prof.unit_label} {n}: resolving remaining hard errors..."},
                    )
                    fixed_err, fix_err_rep = await slop_fix.fix(
                        final_text,
                        rep,
                        book=fresh_b,
                        bdir=bdir,
                        chapter_num=n,
                        include_warnings=False,
                        max_rewrites=16,
                    )
                    events.publish(job.id, {"type": "fix_tells", "report": fix_err_rep})
                    if fixed_err.strip() and fixed_err.strip() != final_text.strip():
                        final_text = fixed_err
                        rep = await _commit_new_prose(
                            book_id,
                            n,
                            final_text,
                            new_status=ChapterStatus.drafted,
                            snapshot_existing=True,
                        )
                if rep["error_count"] == 0:
                    await _commit_new_prose(
                        book_id,
                        n,
                        final_text,
                        new_status=ChapterStatus.polished,
                        snapshot_existing=False,
                    )
                await _update_memory(job, book_id, n, final_text)
                events.publish(job.id, {"type": "artifact", "name": f"chapters/ch_{n:03d}.md"})

        if not job._cancel_requested and body.rerun_audit:
            fresh_final = get_book(book_id)
            if fresh_final:
                events.publish(job.id, {"type": "status", "message": "Re-running Whole-Manuscript Editorial Audit..."})
                res = await manuscript_audit.run_full_audit(fresh_final, bdir, stream_cb=_tok, on_warning=_warn)
                events.publish(job.id, {"type": "artifact", "name": "reports/manuscript_audit.json", "data": res})

    j = jobs.enqueue(book_id=book_id, kind="audit_fix", fn=_run)
    return j.to_dict()
