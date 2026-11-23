from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from app import llm_client
from app.formats import get_profile
from app.pipeline import ai_checker
from app.pipeline._common import WarnCB, foreshadowing_for_chapter, load_prompt, parse_llm_json, warn_if_cut
from app.pipeline.directives import build_directives
from app.storage import atomic_write_json, read_json, read_text
from app.store import Book, chapter_path


def compute_deterministic_audit(book: Book, bdir: Path) -> dict[str, Any]:
    prof = get_profile(book.book_format)
    outline_md = read_text(bdir / "outline.md")
    chapters_stats: list[dict[str, Any]] = []
    simile_counter: Counter[str] = Counter()
    opener_counter: Counter[str] = Counter()
    ending_shapes: list[str] = []
    total_words = 0

    for ch in sorted(book.chapters, key=lambda c: c.number):
        txt = read_text(chapter_path(bdir, ch.number)).strip()
        if not txt:
            chapters_stats.append(
                {"number": ch.number, "title": ch.title, "status": ch.status.value, "word_count": 0, "grade": "—", "tells_per_1000": 0.0}
            )
            continue
        rep = ai_checker.scan(txt, book=book, bdir=bdir, chapter_num=ch.number)
        m = rep["metrics"]
        wc = m["word_count"]
        total_words += wc
        chapters_stats.append(
            {
                "number": ch.number,
                "title": ch.title,
                "status": ch.status.value,
                "word_count": wc,
                "grade": rep["cleanliness_grade"],
                "error_count": rep["error_count"],
                "warning_count": rep["warning_count"],
                "tells_per_1000": m["tells_per_1000"],
                "dialogue_pct": m["dialogue_pct"],
                "fk_grade": m["flesch_kincaid_grade"],
            }
        )
        for sm in re.finditer(r"\b(?:like|as)\s+(?:a|an|the)\s+([a-z]+(?:\s+[a-z]+){1,4})", txt, re.IGNORECASE):
            simile_counter[re.sub(r"\s+", " ", sm.group(0).lower())] += 1
        paras = [p.strip() for p in re.split(r"\n\s*\n", txt) if p.strip() and not p.strip().startswith("#")]
        if paras:
            fw = re.match(r"^[A-Za-z'’-]+", paras[0])
            if fw:
                opener_counter[fw.group(0)] += 1
            last = paras[-1]
            ending_shapes.append("question" if last.endswith("?") else ("short" if len(last.split()) < 14 else "prose"))

    # Audit Foreshadowing ledger plants vs payoffs
    ledger_items: list[dict[str, Any]] = []
    if outline_md:
        for ch in book.chapters:
            for line in foreshadowing_for_chapter(outline_md, ch.number):
                ledger_items.append({"chapter": ch.number, "note": line})

    return {
        "unit_label": prof.unit_label,
        "total_words": total_words,
        "target_words": book.target_words,
        "chapters": chapters_stats,
        "overused_similes": [{"phrase": p, "count": c} for p, c in simile_counter.most_common(12) if c >= 2],
        "repeated_chapter_openers": [{"word": w, "count": c} for w, c in opener_counter.most_common(6) if c >= 2],
        "ending_shapes": ending_shapes,
        "foreshadowing_schedule": ledger_items,
    }


async def run_full_audit(
    book: Book,
    bdir: Path,
    *,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> dict[str, Any]:
    det = compute_deterministic_audit(book, bdir)
    directives = build_directives(book)
    anti_slop = load_prompt("anti_slop.md")
    outline_md = read_text(bdir / "outline.md")
    summary_md = read_text(bdir / "summary.md")
    canon_md = read_text(bdir / "canon.md")
    voice_md = read_text(bdir / "voice.md")

    # Gather opening + midpoint + closing excerpts of each drafted chapter for whole-book flow & ending inspection
    excerpts: list[str] = []
    for ch in sorted(book.chapters, key=lambda c: c.number):
        txt = read_text(chapter_path(bdir, ch.number)).strip()
        if not txt:
            continue
        head = txt[:550]
        mid = txt[len(txt) // 2 - 275 : len(txt) // 2 + 275] if len(txt) > 1500 else ""
        tail = txt[-550:] if len(txt) > 700 else ""
        mid_blk = f"\n...\nMID: {mid}" if mid else ""
        excerpts.append(f"### Unit {ch.number}: {ch.title} ({len(txt.split())} words)\nOPEN: {head}{mid_blk}\n...\nCLOSE: {tail}")

    system = "\n\n---\n\n".join(
        [
            directives,
            "You are a ruthless, candid senior acquiring editor delivering an unvarnished whole-manuscript editorial letter.\n"
            "Be brutally honest — never flatter weak prose. Verify:\n"
            "1. Word count completeness (if total_words < 75% of target_words, verdict MUST be `needs_revision`).\n"
            "2. Seed & Ending Fidelity (did the final chapter deliver the Seed's exact promised conflict and resolution on-page without inventing unrequested crimes, arrests, genres, or cliffhangers?).\n"
            "3. Continuity, remaining-day calendar subtraction math, item quantities, collateral vs sale precision, and regional/era realism.\n"
            "4. AI voice tics, filter words, stiff/clinical passive prose, low dialogue share, and repetitive openings.\n"
            "Return ONLY valid JSON:\n"
            '{"publishability_verdict": "ready|needs_polish|needs_revision", "executive_summary": "...", '
            '"strengths": ["..."], "structural_issues": ["..."], "continuity_risks": ["..."], '
            '"ai_style_patterns": ["..."], "top_fixes": [{"chapter": 1, "action": "..."}]}',
            anti_slop,
        ]
    )

    seed_md = read_text(bdir / "seed.txt").strip()
    user = "\n\n".join(
        [
            f"BOOK: {book.title}",
            f"SEED PREMISE:\n{seed_md[:6000]}",
            f"VOICE SUMMARY:\n{voice_md[:2000]}",
            f"CANON:\n{canon_md[:3500]}",
            f"FULL STORY SUMMARY:\n{summary_md[:6000]}",
            f"OUTLINE:\n{outline_md[:4500]}",
            "MECHANICAL ANALYTICS:\n" + str(det),
            "CHAPTER OPENINGS & ENDINGS:\n" + "\n\n".join(excerpts[:18]),
        ]
    )

    res = await llm_client.complete(
        system=system,
        user=user,
        task="audit",
        max_tokens=4000,
        creativity="low",
        json_mode=True,
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Manuscript audit")

    try:
        editorial = parse_llm_json(res.text)
        if not isinstance(editorial, dict):
            raise ValueError("Invalid audit root")
    except Exception as exc:
        if on_warning:
            on_warning(f"Editorial letter JSON fallback: {exc}")
        editorial = {
            "publishability_verdict": "needs_revision",
            "executive_summary": res.text.strip()[:1500] or "Audit completed with deterministic analytics only.",
            "strengths": [],
            "structural_issues": [],
            "continuity_risks": [],
            "ai_style_patterns": [],
            "top_fixes": [],
        }

    payload = {"deterministic": det, "editorial": editorial}
    atomic_write_json(bdir / "reports" / "manuscript_audit.json", payload)
    return payload


def load_audit(bdir: Path) -> dict[str, Any] | None:
    data = read_json(bdir / "reports" / "manuscript_audit.json")
    return data if isinstance(data, dict) else None
